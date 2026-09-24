#!/usr/bin/env python3
"""Parent-side sandbox. Runs a scraper and validates everything it emits.

Both hand-written platform adapters and model-generated scripts run through
here, unchanged. That is deliberate: one execution path means the rate limiting,
the identifying user agent, the emit validation and the failure taxonomy apply
uniformly, and an adapter cannot quietly acquire privileges a generated script
does not have.

Three separate things constrain the child:

  identity   the unprivileged service account in production; root-run manual
             tooling drops again to the dedicated osbscraper account
  budget     wall-clock timeout plus an address-space rlimit, so a runaway
             script cannot starve the 2GB box that also serves the website
  interface  no DATABASE_URL and no Spaces keys in the child environment. The
             only channel out is one JSON line per emitted meeting, and every
             field is re-validated here before it reaches a query

The last one matters most. A credential the child never holds cannot be misused,
and re-validating in the parent means a buggy or hostile scraper cannot cause a
write we did not intend.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import resource
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "runner.py"
SANDBOX_USER = os.environ.get("SCRAPER_USER", "osbscraper")
WORKDIR = Path(os.environ.get(
    "SCRAPER_WORKDIR",
    str(Path(os.environ.get("DATA_DIR", "/tmp/ourschoolboard")) / "scraper-tmp"),
))

DEFAULT_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT", "300"))
DEFAULT_MEMORY_MB = int(os.environ.get("SCRAPER_MEMORY_MB", "512"))
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

VALID_KINDS = {"agenda", "minutes", "packet", "video", "other"}

# Map a child exception class onto the failure taxonomy the runs table records.
# Classification is what makes "where are we failing" answerable across a
# thousand agencies; an undifferentiated error column would not be.
FAILURE_KINDS = {
    "ConnectionError": "fetch_error", "ConnectTimeout": "fetch_error",
    "ReadTimeout": "fetch_error", "Timeout": "fetch_error",
    "HTTPError": "fetch_error", "TooManyRedirects": "fetch_error",
    "SSLError": "fetch_error", "ProxyError": "fetch_error",
    "UnsafeUrlError": "fetch_error",
    "BrowserFetchError": "fetch_error",
    "BrowserRuntimeError": "sandbox_error",
    "AttributeError": "parse_error", "IndexError": "parse_error",
    "KeyError": "parse_error", "TypeError": "parse_error",
    "ValueError": "parse_error", "JSONDecodeError": "parse_error",
}


def _limits(memory_mb: int, *, browser_enabled: bool = False):
    def apply():
        if not browser_enabled:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        # Chromium reserves a large sparse virtual address range, so RLIMIT_AS
        # prevents it from starting even when resident memory is modest. For an
        # explicitly browser-enabled scraper, the systemd cgroup's MemoryMax is
        # the real-memory boundary; the wall timeout and process cap remain.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # Cap processes too: a memory limit alone is escapable by forking.
        resource.setrlimit(resource.RLIMIT_NPROC, (256, 256))
    return apply


def _child_scratch(tmp_path: Path) -> Path:
    """Create the only directory generated code may write inside its run."""
    child = tmp_path / "child"
    child.mkdir(mode=0o700)
    if os.geteuid() == 0:
        account = pwd.getpwnam(SANDBOX_USER)
        os.chown(child, account.pw_uid, account.pw_gid)
    return child


def _write_browser_proxy_handoff(child_scratch: Path) -> Path | None:
    """Write Bright Data credentials once without adding them to child env."""
    names = {
        "server": "BRIGHTDATA_PROXY",
        "username": "BRIGHTDATA_PROXY_USER",
        "password": "BRIGHTDATA_PROXY_PASS",
    }
    values = {key: os.environ.get(name, "") for key, name in names.items()}
    if not all(values.values()):
        return None
    path = child_scratch / "browser-proxy.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(values, handle)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.geteuid() == 0:
        account = pwd.getpwnam(SANDBOX_USER)
        os.chown(path, account.pw_uid, account.pw_gid)
    return path


def _write_scraping_browser_handoff(child_scratch: Path) -> Path | None:
    """Write the Bright Data Scraping Browser endpoint once, same pattern as
    the ISP proxy handoff above but for a single CDP URL that already embeds
    its own credentials.

    A different product from the ISP proxy: a real, already-running remote
    Chromium reached over CDP/WebSocket, offered for sources where the ISP
    proxy's plain Chromium session still can't get past a bot-mitigation wall,
    or where reaching the content needs real page interaction (a click, a
    form-postback) rather than only a render. It stays opt-in per scraper via
    ``config.browser.provider`` and is never used to bypass prohibiting terms.
    """
    host = os.environ.get("BRIGHTDATA_SCRAPING_BROWSER_HOST", "brd.superproxy.io:9222")
    user = os.environ.get("BRIGHTDATA_SCRAPING_BROWSER_USER", "")
    password = os.environ.get("BRIGHTDATA_WEB_ACCESS_PASS", "")
    if not user or not password:
        return None
    endpoint = f"wss://{user}:{password}@{host}"
    path = child_scratch / "scraping-browser.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"endpoint": endpoint}, handle)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    if os.geteuid() == 0:
        account = pwd.getpwnam(SANDBOX_USER)
        os.chown(path, account.pw_uid, account.pw_gid)
    return path


def _runtime_workdir() -> Path:
    # DATA_DIR is intentionally private to the service account. Root-run
    # onboarding tools drop to osbscraper, which cannot traverse that private
    # path, so use a separate root-owned/traversable temp base for those runs.
    if os.geteuid() == 0:
        return Path(os.environ.get(
            "SCRAPER_ROOT_WORKDIR", "/tmp/ourschoolboard-scraper"
        ))
    return WORKDIR


def _child_runner(tmp_path: Path) -> Path:
    """Copy the trusted harness below a path the dropped UID can traverse."""
    if os.geteuid() != 0:
        return RUNNER
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o755)
    for source in (RUNNER, HERE / "safe_http.py"):
        destination = runtime / source.name
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o644)
    return runtime / RUNNER.name


def parse_meeting_date(value) -> date | None:
    """Accept the shapes a scraper is likely to produce; reject nonsense.

    Being liberal is fine because the value is validated rather than trusted,
    but an unparseable date must become None rather than raise — one odd row
    should not kill an otherwise good run.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y",
                "%d %B %Y", "%Y/%m/%d", "%m-%d-%Y", "%B %d %Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            break
        except ValueError:
            continue
    else:
        match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
        if not match:
            return None
        try:
            parsed = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    if not (date(1990, 1, 1) <= parsed <= date(date.today().year + 2, 12, 31)):
        return None
    return parsed


def validate(record: dict) -> dict | None:
    """Re-check every field arriving from the child. Nothing here is trusted."""
    url = (record.get("document_url") or "").strip()
    if not re.match(r"^https?://[^\s<>\"]{4,2000}$", url):
        return None
    kind = record.get("kind")
    if kind not in VALID_KINDS:
        kind = "other"
    extra = record.get("extra") if isinstance(record.get("extra"), dict) else {}
    explicit_filename = str(extra.get("document_filename") or "").strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,180}$", explicit_filename):
        explicit_filename = None
    return {
        "document_url": url,
        "meeting_date": parse_meeting_date(record.get("meeting_date")),
        "title": (record.get("title") or "").strip()[:300] or None,
        "kind": kind,
        "document_filename": explicit_filename,
    }


def run_scraper(source: str, agency: dict, seen_urls: set[str], *,
                timeout: int = DEFAULT_TIMEOUT,
                memory_mb: int = DEFAULT_MEMORY_MB,
                config: dict | None = None) -> dict:
    """Execute one scraper. Never raises for scraper-caused failures."""
    workdir = _runtime_workdir()
    workdir.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        os.chmod(workdir, 0o755)

    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        tmp_path = Path(tmp)
        script_file = tmp_path / "scraper.py"
        input_file = tmp_path / "input.json"
        script_file.write_text(source, encoding="utf-8")
        input_file.write_text(json.dumps({
            "agency": {k: agency.get(k) for k in
                       ("id", "name", "website", "board_page_url", "nces_leaid")},
            "config": config or {},
            "seen_urls": sorted(seen_urls),
        }), encoding="utf-8")
        os.chmod(tmp_path, 0o755)
        os.chmod(script_file, 0o644)
        os.chmod(input_file, 0o644)
        child_scratch = _child_scratch(tmp_path)
        browser_enabled = bool(
            isinstance(config, dict)
            and isinstance(config.get("browser"), dict)
            and config["browser"].get("enabled") is True
        )

        # A deliberately minimal environment. Note what is absent: DATABASE_URL,
        # DO_SPACES_*, ANTHROPIC_API_KEY, OPENAI_API_KEY, RESEND_API_KEY, and Bright Data
        # credentials. Browser credentials use a private one-shot file consumed
        # by the trusted runner before model-authored code is loaded.
        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(child_scratch),
            "TMPDIR": str(child_scratch),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SCRAPER_USER_AGENT": os.environ.get(
                "SCRAPER_USER_AGENT",
                DEFAULT_USER_AGENT),
            "SCRAPER_MIN_INTERVAL": os.environ.get("SCRAPER_MIN_INTERVAL", "1.0"),
            # A fixed, root-owned browser cache works with ProtectHome=true.
            # Do not pass any broader parent environment into generated code.
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH", "/opt/ourschoolboard/playwright"
            ),
            "XDG_CACHE_HOME": str(child_scratch / ".cache"),
            "XDG_CONFIG_HOME": str(child_scratch / ".config"),
        }
        if browser_enabled:
            provider = config["browser"].get("provider", "isp_proxy")
            if provider == "scraping_browser":
                scraping_browser_file = _write_scraping_browser_handoff(child_scratch)
                if scraping_browser_file is not None:
                    env["SCRAPER_SCRAPING_BROWSER_FILE"] = str(scraping_browser_file)
            elif provider == "isp_proxy":
                proxy_file = _write_browser_proxy_handoff(child_scratch)
                if proxy_file is not None:
                    env["SCRAPER_BROWSER_PROXY_FILE"] = str(proxy_file)
        chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH")
        if chromium_path:
            env["PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH"] = chromium_path

        runner = _child_runner(tmp_path)
        command = [sys.executable, str(runner), str(script_file), str(input_file)]
        # The live systemd parent already runs as the unprivileged
        # ``ourschoolboard`` service account. Root-run onboarding/manual tools
        # get an additional UID drop so they never execute generated source as
        # root.
        if os.geteuid() == 0:
            command = ["setpriv", "--reuid", SANDBOX_USER, "--regid", SANDBOX_USER,
                       "--clear-groups", "--"] + command

        meetings: list[dict] = []
        logs: list[str] = []
        warnings: list[str] = []
        error: dict | None = None
        summary: dict = {}
        rejected = 0

        try:
            process = subprocess.run(
                command, env=env, cwd=tmp, capture_output=True, text=True,
                timeout=timeout,
                preexec_fn=_limits(memory_mb, browser_enabled=browser_enabled),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "failure_kind": "timeout",
                    "error": f"scraper exceeded {timeout}s",
                    "meetings": [], "logs": logs, "warnings": [], "summary": {},
                    "rejected": 0}

        for line in (process.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logs.append(line[:300])       # stray prints are noise, not data
                continue
            kind = message.get("type")
            if kind == "meeting":
                clean = validate(message)
                if clean:
                    meetings.append(clean)
                else:
                    rejected += 1
            elif kind == "log":
                logs.append(message.get("message", "")[:300])
            elif kind == "warn":
                warnings.append(message.get("message", "")[:300])
            elif kind == "error":
                error = message
            elif kind == "done":
                summary = message

        base = {"meetings": meetings, "logs": logs, "warnings": warnings,
                "summary": summary, "rejected": rejected}

        if error:
            return {**base, "ok": False,
                    "failure_kind": FAILURE_KINDS.get(error.get("error_class"), "parse_error"),
                    "error": f"{error.get('error_class')}: {error.get('message')}",
                    "traceback": error.get("traceback")}

        if process.returncode != 0:
            stderr = (process.stderr or "").strip()[-1500:]
            return {**base, "ok": False, "failure_kind": "sandbox_error",
                    "error": f"exit {process.returncode}: {stderr}"}

        # A clean exit that found nothing is still a failure. A district
        # publishing nothing at all is far less likely than a selector that
        # stopped matching, and treating the two alike is how a pipeline dies
        # quietly.
        if not meetings:
            return {**base, "ok": False, "failure_kind": "empty_result",
                    "error": "scraper completed but emitted no meetings"}

        return {**base, "ok": True, "failure_kind": None, "error": None}
