#!/usr/bin/env python3
"""Child-side harness. Runs one generated scraper and streams what it finds.

Invoked by sandbox.py as an unprivileged user with no database URL and no
object-storage keys in its environment. This process is assumed to be running
untrusted code — the scrapers are model-authored and stored in a database, so
"untrusted" is a statement about provenance, not about intent.

The scraper's whole contract:

    def scrape(ctx):
        for row in ...:
            ctx.emit_meeting(
                meeting_date="2026-03-11",          # ISO date, or None
                title="Regular Board Meeting",
                kind="minutes",                      # agenda|minutes|packet|video|other
                document_url="https://…/minutes.pdf",
            )

`ctx.emit_meeting` is the only way data leaves the sandbox. It writes one JSON
line to stdout; the parent validates every field, decides what is new, downloads
the bytes itself and performs every database write. So the capability the
scraper holds is "append a candidate for consideration" — not "write a row".
It cannot update, cannot delete, cannot touch a table it was not meant to, and
cannot reach any other project's data in the shared database.

Fetching goes through ctx.get() rather than requests directly, so rate limiting,
the identifying user agent and a response size cap apply even if the generated
code does not think to add them.
"""
from __future__ import annotations

import json
import os
import runpy
import stat
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from safe_http import UnsafeUrlError, assert_public_url, safe_get

USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
MAX_EMITS = int(os.environ.get("SCRAPER_MAX_EMITS", "2000"))
MAX_FETCHES = int(os.environ.get("SCRAPER_MAX_FETCHES", "300"))
MAX_BYTES = int(os.environ.get("SCRAPER_MAX_BYTES", str(20 * 1024 * 1024)))
MIN_INTERVAL = float(os.environ.get("SCRAPER_MIN_INTERVAL", "1.0"))
RETRYABLE_STATUS = (429, 500, 502, 503, 504)

BROWSER_DEFAULTS = {
    "max_fetches": 12,
    "max_network_requests": 300,
    "timeout_seconds": 30,
    "max_response_bytes": 5 * 1024 * 1024,
}
BROWSER_HARD_LIMITS = {
    "max_fetches": 50,
    "max_network_requests": 1000,
    "timeout_seconds": 60,
    "max_response_bytes": 20 * 1024 * 1024,
}
BROWSER_WAIT_UNTIL = {"commit", "domcontentloaded", "load", "networkidle"}
BROWSER_PROVIDERS = {"direct", "isp_proxy", "scraping_browser"}

# A bounded, declarative action vocabulary — not a live page handle. The
# scraper describes intent ("click this selector"); the trusted runner is the
# only thing that ever holds a Playwright Page. This is what lets a source
# that needs a click or a form-postback stay inside the same capability model
# as a plain navigate-and-read: the scraper still cannot execute arbitrary
# browser automation, only compose from a short allowed list.
MAX_BROWSER_ACTIONS = 30
MAX_BROWSER_ACTION_WAIT_MS = 10_000
BROWSER_ACTION_TYPES = {"click", "fill", "press", "wait_for_selector", "wait_ms"}

VALID_KINDS = {"agenda", "minutes", "packet", "video", "other"}


class BrowserFetchError(RuntimeError):
    pass


class BrowserRuntimeError(RuntimeError):
    pass


def _take_browser_proxy() -> dict[str, str]:
    """Consume the parent's one-shot Bright Data credential handoff.

    Generated scraper code runs later in this process. Keeping credentials out
    of the child environment and unlinking this file before ``runpy`` starts
    prevents ordinary scraper code from reading or accidentally logging them.
    """
    raw_path = os.environ.pop("SCRAPER_BROWSER_PROXY_FILE", "")
    if not raw_path:
        raise BrowserRuntimeError(
            "browser_get requires the Bright Data proxy to be configured"
        )
    path = Path(raw_path)
    scratch = Path(os.environ.get("TMPDIR", "")).resolve()
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise BrowserRuntimeError("invalid browser proxy credential handoff")
        if resolved.parent != scratch:
            raise BrowserRuntimeError("browser proxy credential file is outside TMPDIR")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except BrowserRuntimeError:
        raise
    except Exception as exc:
        raise BrowserRuntimeError("invalid browser proxy credential handoff") from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    server = str(payload.get("server") or "").strip()
    username = str(payload.get("username") or "")
    password = str(payload.get("password") or "")
    if "://" not in server:
        server = f"http://{server}"
    parsed = urlsplit(server)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not username
        or not password
    ):
        raise BrowserRuntimeError("invalid Bright Data proxy configuration")
    try:
        port = parsed.port
    except ValueError as exc:
        raise BrowserRuntimeError("invalid Bright Data proxy configuration") from exc
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    normalized = f"{parsed.scheme}://{host}"
    if port is not None:
        normalized += f":{port}"
    return {"server": normalized, "username": username, "password": password}


def _take_scraping_browser_endpoint() -> str:
    """Consume the parent's one-shot Scraping Browser credential handoff.

    Mirrors ``_take_browser_proxy``: the endpoint (which embeds its own
    username/password, since Bright Data's Scraping Browser is a single CDP
    URL rather than a proxy triple) is read from a private file and that file
    is unlinked before ``runpy`` starts, so generated scraper code never sees
    it even transiently.
    """
    raw_path = os.environ.pop("SCRAPER_SCRAPING_BROWSER_FILE", "")
    if not raw_path:
        raise BrowserRuntimeError(
            "browser_get requires the Scraping Browser endpoint to be configured"
        )
    path = Path(raw_path)
    scratch = Path(os.environ.get("TMPDIR", "")).resolve()
    try:
        resolved = path.resolve(strict=True)
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode):
            raise BrowserRuntimeError("invalid Scraping Browser credential handoff")
        if resolved.parent != scratch:
            raise BrowserRuntimeError("Scraping Browser credential file is outside TMPDIR")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except BrowserRuntimeError:
        raise
    except Exception as exc:
        raise BrowserRuntimeError("invalid Scraping Browser credential handoff") from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    endpoint = str(payload.get("endpoint") or "").strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or not parsed.username
        or not parsed.password
    ):
        raise BrowserRuntimeError("invalid Scraping Browser endpoint configuration")
    return endpoint


def _validate_browser_actions(actions) -> list[tuple[str, object]]:
    """Bound and normalize a declarative action list before it ever runs.

    Each item is a single-key mapping naming one of BROWSER_ACTION_TYPES. This
    is intentionally not "run this callable" — the scraper never gets a page
    handle, only a short vocabulary the trusted runner interprets itself, so
    the sandbox's capability model (append candidates, never drive arbitrary
    automation) still holds for interactive sources.
    """
    if actions is None:
        return []
    if not isinstance(actions, list) or len(actions) > MAX_BROWSER_ACTIONS:
        raise ValueError(
            f"browser actions must be a list of at most {MAX_BROWSER_ACTIONS} items"
        )
    cleaned: list[tuple[str, object]] = []
    for item in actions:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("each browser action must be a single-key object")
        ((action_type, value),) = item.items()
        if action_type not in BROWSER_ACTION_TYPES:
            raise ValueError(f"unsupported browser action: {action_type}")
        if action_type == "fill":
            if (
                not isinstance(value, list) or len(value) != 2
                or not all(isinstance(part, str) for part in value)
            ):
                raise ValueError("browser action 'fill' requires [selector, value]")
            selector, text = value
            if not selector or len(selector) > 500 or len(text) > 2000:
                raise ValueError("browser action 'fill' selector/value out of bounds")
        elif action_type == "wait_ms":
            if (
                isinstance(value, bool) or not isinstance(value, int)
                or not 0 < value <= MAX_BROWSER_ACTION_WAIT_MS
            ):
                raise ValueError(
                    f"browser action 'wait_ms' must be 1-{MAX_BROWSER_ACTION_WAIT_MS}"
                )
        else:  # click, press, wait_for_selector — each takes one short string
            if not isinstance(value, str) or not value or len(value) > 500:
                raise ValueError(
                    f"browser action '{action_type}' requires a 1-500 character string"
                )
        cleaned.append((action_type, value))
    return cleaned


def _http_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("browser_get requires an http(s) URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("browser_get does not accept credentials in URLs")
    return assert_public_url(value)


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserRuntimeError(
            "browser_get is enabled but Playwright is not installed; "
            "run deploy/install-playwright-runtime"
        ) from exc
    return sync_playwright


class BrowserResponse:
    """Small requests-compatible response returned by ``ctx.browser_get``."""

    def __init__(self, *, url: str, status_code: int,
                 headers: dict[str, str], content: bytes):
        self.url = url
        self.status_code = status_code
        self.headers = headers
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(
                f"{self.status_code} response from browser navigation: {self.url}"
            )

    def json(self):
        return json.loads(self.text)


def _out(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, default=str) + "\n")
    sys.stdout.flush()


DOCUMENT_FLOOR_TOLERANCE = 2
DOCUMENT_FLOOR_KEYS = frozenset({"minimum_documents", "minimum_records"})


class RuntimeConfig(dict):
    """Expose rolling-window document floors with a small expiry tolerance.

    Authored scrapers keep recording the reviewed baseline in stored config.
    At runtime, only total document/record floors are relaxed; meeting-type,
    source-structure, selector, and date-range guards remain unchanged.
    """

    @staticmethod
    def _runtime_value(key, value):
        if (key in DOCUMENT_FLOOR_KEYS and isinstance(value, int)
                and not isinstance(value, bool)):
            return max(1, value - DOCUMENT_FLOOR_TOLERANCE)
        return value

    def __getitem__(self, key):
        return self._runtime_value(key, super().__getitem__(key))

    def get(self, key, default=None):
        if key not in self:
            return default
        return self[key]


class Context:
    def __init__(self, agency: dict, seen_urls: set[str], config: dict | None = None,
                 *, browser_proxy: dict[str, str] | None = None,
                 scraping_browser_endpoint: str | None = None):
        self.agency = agency
        self.board_page_url = agency.get("board_page_url")
        self.website = agency.get("website")
        # Source pages, portal ids and selectors are data, not secrets. Keeping
        # them in config lets one script shape stay reusable and keeps URLs out
        # of generated code where they are harder to inspect or update.
        self.config = RuntimeConfig(config or {})
        self._seen = seen_urls
        self._emits = 0
        self._fetches = 0
        self._last_fetch = 0.0
        self._browser_fetches = 0
        self._browser_network_requests = 0
        self._browser_network_limit_hit = False
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._browser_proxy = browser_proxy
        self._scraping_browser_endpoint = scraping_browser_endpoint
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers["User-Agent"] = USER_AGENT
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=3, connect=3, read=3, status=3, backoff_factor=0.5,
            status_forcelist=RETRYABLE_STATUS,
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
        )))

    # ---- the write capability ----------------------------------------------

    def emit_meeting(self, document_url: str, meeting_date=None, title=None,
                     kind: str = "other", **extra) -> bool:
        """Offer one meeting document to the parent. Returns False if skipped.

        Validation happens here *and* again in the parent. Here so the scraper
        gets immediate feedback and a typo does not silently vanish; in the
        parent because nothing arriving from this process may be trusted.
        """
        if self._emits >= MAX_EMITS:
            raise RuntimeError(f"emit limit reached ({MAX_EMITS}) — scraper is probably looping")
        url = (document_url or "").strip()
        if not url.startswith(("http://", "https://")):
            _out({"type": "warn", "message": f"ignored emit with bad url: {url[:120]!r}"})
            return False
        if kind not in VALID_KINDS:
            kind = "other"
        self._emits += 1
        _out({
            "type": "meeting",
            "document_url": url,
            "meeting_date": str(meeting_date) if meeting_date else None,
            "title": (title or "").strip()[:300] or None,
            "kind": kind,
            "extra": {k: str(v)[:200] for k, v in extra.items()},
        })
        return True

    # ---- incremental --------------------------------------------------------

    def already_have(self, document_url: str) -> bool:
        """True if this document is already stored.

        The parent skips duplicates regardless, so this is an optimisation the
        scraper may use to avoid pagination it does not need — not a correctness
        requirement. Dedupe must not depend on generated code getting it right.
        """
        return (document_url or "").strip() in self._seen

    # ---- fetching -----------------------------------------------------------

    def get(self, url: str, **kwargs):
        if self._fetches >= MAX_FETCHES:
            raise RuntimeError(f"fetch limit reached ({MAX_FETCHES})")
        # Politeness is enforced here, not left to the generated code. A model
        # writing a tight loop against a school district's server is the failure
        # mode most likely to get us blocked, and it would be our fault.
        wait = MIN_INTERVAL - (time.monotonic() - self._last_fetch)
        if wait > 0:
            time.sleep(wait)
        self._fetches += 1
        self._last_fetch = time.monotonic()
        kwargs.setdefault("timeout", 30)
        response = safe_get(self.session, url, **kwargs)
        if len(response.content) > MAX_BYTES:
            raise RuntimeError(f"response exceeded {MAX_BYTES} bytes: {url}")
        return response

    def _browser_settings(self) -> dict:
        raw = self.config.get("browser")
        if not isinstance(raw, dict) or raw.get("enabled") is not True:
            raise BrowserRuntimeError(
                "browser_get is disabled; register this scraper with "
                "config.browser.enabled=true"
            )
        settings = dict(BROWSER_DEFAULTS)
        for name, maximum in BROWSER_HARD_LIMITS.items():
            value = raw.get(name, settings[name])
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise RuntimeError(f"invalid config.browser.{name}")
            settings[name] = value
        provider = raw.get("provider", "isp_proxy")
        if provider not in BROWSER_PROVIDERS:
            raise RuntimeError(f"invalid config.browser.provider: {provider!r}")
        settings["provider"] = provider
        return settings

    def _ensure_browser(self, settings: dict) -> None:
        if self._browser_context is not None:
            return
        provider = settings["provider"]
        if provider == "scraping_browser":
            endpoint = self._scraping_browser_endpoint
            self._scraping_browser_endpoint = None
            if not endpoint:
                raise BrowserRuntimeError(
                    "browser_get requires the Scraping Browser endpoint to be configured"
                )
            self._playwright = _load_playwright()().start()
            # connect_over_cdp attaches to an already-running remote browser —
            # there is no local process to launch and no proxy dict to scrub,
            # but the endpoint itself carries the credential and must not be
            # retained anywhere after this call.
            self._browser = self._playwright.chromium.connect_over_cdp(
                endpoint, timeout=settings["timeout_seconds"] * 1000,
            )
            self._browser_context = (
                self._browser.contexts[0] if self._browser.contexts
                else self._browser.new_context(
                    user_agent=USER_AGENT,
                    accept_downloads=False,
                    service_workers="block",
                )
            )
        else:
            if provider == "isp_proxy" and not self._browser_proxy:
                raise BrowserRuntimeError(
                    "browser_get requires the Bright Data proxy to be configured"
                )
            proxy = self._browser_proxy
            self._playwright = _load_playwright()().start()
            try:
                executable_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH") or None
                launch_options = {
                    "headless": True,
                    # The process already runs inside the service/scraper
                    # confinement. These flags avoid assumptions about a
                    # writable /dev/shm or a usable setuid sandbox inside that
                    # outer sandbox.
                    "args": ["--disable-dev-shm-usage", "--no-sandbox"],
                }
                if provider == "isp_proxy":
                    launch_options["proxy"] = dict(proxy)
                if executable_path:
                    launch_options["executable_path"] = executable_path
                self._browser = self._playwright.chromium.launch(**launch_options)
            finally:
                # Do not retain proxy credentials on the object exposed to
                # scraper code after Chromium has consumed them.
                for key in list(proxy or {}):
                    proxy[key] = ""
                self._browser_proxy = None
            self._browser_context = self._browser.new_context(
                user_agent=USER_AGENT,
                accept_downloads=False,
                service_workers="block",
            )
        # WebSockets do not pass through ordinary request routing in
        # Playwright. Scraper discovery does not need them, so close them rather
        # than leave a second, unvalidated network path to the page.
        if hasattr(self._browser_context, "route_web_socket"):
            self._browser_context.route_web_socket(
                "**/*", lambda websocket: websocket.close()
            )
        self._browser_context.set_default_timeout(settings["timeout_seconds"] * 1000)

    def browser_get(self, url: str, *, wait_until: str = "domcontentloaded",
                    selector: str | None = None, actions=None) -> BrowserResponse:
        """Render one public HTTP(S) page with bounded, opt-in Chromium.

        Ordinary HTTP remains the default. This is intended for official pages
        whose public document links are populated by JavaScript; it does not
        add authentication, cookies supplied by scraper code, or terms bypasses.

        ``actions`` is an optional bounded list of clicks/fills/waits, run in
        order after the initial navigation and before ``selector`` (if given)
        and the final content read — for a source whose real listing only
        appears after a click or a postback-driven control, not just after
        JavaScript finishes running. See BROWSER_ACTION_TYPES for the allowed
        shapes. Available with either ``config.browser.provider``.
        """
        settings = self._browser_settings()
        if wait_until not in BROWSER_WAIT_UNTIL:
            raise ValueError(f"unsupported browser wait_until: {wait_until}")
        if selector is not None and (not isinstance(selector, str) or not selector or len(selector) > 500):
            raise ValueError("browser selector must be 1-500 characters")
        cleaned_actions = _validate_browser_actions(actions)
        target = _http_url(url)
        if self._browser_fetches >= settings["max_fetches"]:
            raise BrowserFetchError(
                f"browser fetch limit reached ({settings['max_fetches']})"
            )

        self._browser_fetches += 1
        self._ensure_browser(settings)
        page = self._browser_context.new_page()

        def route_request(route, request):
            parsed = urlsplit(request.url)
            if parsed.scheme not in {"http", "https"}:
                route.abort()
                return
            self._browser_network_requests += 1
            if self._browser_network_requests > settings["max_network_requests"]:
                self._browser_network_limit_hit = True
                route.abort()
                return
            # Discovery needs the DOM and its scripts/XHR, not large visual media.
            if request.resource_type in {"image", "media", "font"}:
                route.abort()
                return
            try:
                assert_public_url(request.url)
            except UnsafeUrlError as exc:
                self._browser_network_limit_hit = True
                self._browser_unsafe_url = str(exc)
                route.abort()
                return
            route.continue_()

        page.route("**/*", route_request)
        try:
            response = page.goto(
                target,
                wait_until=wait_until,
                timeout=settings["timeout_seconds"] * 1000,
            )
            if response is None:
                raise BrowserFetchError(
                    f"browser navigation returned no HTTP response: {target}"
                )
            action_timeout = settings["timeout_seconds"] * 1000
            for action_type, value in cleaned_actions:
                if action_type == "click":
                    page.locator(value).first.click(timeout=action_timeout)
                elif action_type == "fill":
                    field_selector, text = value
                    page.locator(field_selector).first.fill(text, timeout=action_timeout)
                elif action_type == "press":
                    page.keyboard.press(value)
                elif action_type == "wait_for_selector":
                    page.wait_for_selector(value, timeout=action_timeout)
                elif action_type == "wait_ms":
                    page.wait_for_timeout(value)
            if selector:
                page.wait_for_selector(
                    selector, timeout=settings["timeout_seconds"] * 1000
                )
            if self._browser_network_limit_hit:
                if getattr(self, "_browser_unsafe_url", None):
                    raise BrowserFetchError(
                        f"browser blocked unsafe network request: {self._browser_unsafe_url}"
                    )
                raise BrowserFetchError(
                    "browser network request limit reached "
                    f"({settings['max_network_requests']})"
                )
            final_url = _http_url(page.url)
            content = page.content().encode("utf-8")
            if len(content) > settings["max_response_bytes"]:
                raise BrowserFetchError(
                    "browser response exceeded "
                    f"{settings['max_response_bytes']} bytes: {final_url}"
                )
            return BrowserResponse(
                url=final_url,
                status_code=response.status,
                headers=dict(response.headers),
                content=content,
            )
        except BrowserFetchError:
            raise
        except Exception as exc:
            if getattr(self, "_browser_unsafe_url", None):
                raise BrowserFetchError(
                    f"browser blocked unsafe network request: {self._browser_unsafe_url}"
                ) from exc
            raise BrowserFetchError(f"browser navigation failed: {exc}") from exc
        finally:
            page.close()

    def close(self) -> None:
        for resource in (self._browser_context, self._browser):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self.session.close()

    def log(self, *parts) -> None:
        _out({"type": "log", "message": " ".join(str(p) for p in parts)[:1000]})


def main() -> None:
    script_path = sys.argv[1]
    payload = json.load(open(sys.argv[2], encoding="utf-8"))
    ctx = Context(payload["agency"], set(payload.get("seen_urls") or []),
                  payload.get("config"))

    started = time.time()
    try:
        browser = (payload.get("config") or {}).get("browser")
        if isinstance(browser, dict) and browser.get("enabled") is True:
            provider = browser.get("provider", "isp_proxy")
            if provider == "scraping_browser":
                ctx._scraping_browser_endpoint = _take_scraping_browser_endpoint()
            elif provider == "isp_proxy":
                ctx._browser_proxy = _take_browser_proxy()
            # Consume and scrub the credential before model-authored code is
            # loaded. browser_get reuses this already-connected context.
            ctx._ensure_browser(ctx._browser_settings())
        namespace = runpy.run_path(script_path)
        entry = namespace.get("scrape")
        if not callable(entry):
            raise RuntimeError("script defines no callable scrape(ctx)")
        entry(ctx)
    except Exception as exc:
        _out({
            "type": "error",
            # The class name is the most reliable signal for classifying the
            # failure; the parent maps it onto a failure_kind.
            "error_class": type(exc).__name__,
            "message": str(exc)[:2000],
            "traceback": traceback.format_exc()[-4000:],
        })
        _out({"type": "done", "ok": False, "emits": ctx._emits,
              "fetches": ctx._fetches, "browser_fetches": ctx._browser_fetches,
              "seconds": round(time.time() - started, 1)})
        sys.exit(1)
    finally:
        ctx.close()

    _out({"type": "done", "ok": True, "emits": ctx._emits,
          "fetches": ctx._fetches, "browser_fetches": ctx._browser_fetches,
          "seconds": round(time.time() - started, 1)})


if __name__ == "__main__":
    main()
