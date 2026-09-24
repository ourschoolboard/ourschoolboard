import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_runner_browser", PIPELINE / "runner.py")
RUNNER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUNNER
SPEC.loader.exec_module(RUNNER)


class FakeRoute:
    def __init__(self):
        self.action = None

    def abort(self):
        self.action = "abort"

    def continue_(self):
        self.action = "continue"


class FakeRequest:
    url = "https://district.example/meetings"
    resource_type = "document"


class FakeResponse:
    status = 200
    headers = {"content-type": "text/html"}


class FakeKeyboard:
    def __init__(self):
        self.pressed = []

    def press(self, key):
        self.pressed.append(key)


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def click(self, **_kwargs):
        self.page.clicks.append(self.selector)

    def fill(self, value, **_kwargs):
        self.page.fills.append((self.selector, value))


class FakePage:
    url = "https://district.example/meetings"

    def __init__(self, html="<html><div class='meeting'>Ready</div></html>",
                 request_url="https://district.example/meetings"):
        self.html = html
        self.request_url = request_url
        self.route_handler = None
        self.closed = False
        self.selector = None
        self.clicks = []
        self.fills = []
        self.waited_ms = []
        self.keyboard = FakeKeyboard()

    def route(self, _pattern, handler):
        self.route_handler = handler

    def goto(self, _url, **_kwargs):
        route = FakeRoute()
        request = FakeRequest()
        request.url = self.request_url
        self.route_handler(route, request)
        if route.action != "continue":
            raise RuntimeError("navigation was unexpectedly blocked")
        return FakeResponse()

    def locator(self, selector):
        return FakeLocator(self, selector)

    def wait_for_selector(self, selector, **_kwargs):
        self.selector = selector

    def wait_for_timeout(self, ms):
        self.waited_ms.append(ms)

    def content(self):
        return self.html

    def close(self):
        self.closed = True


class FakeBrowserContext:
    def __init__(self, page):
        self.page = page
        self.timeout = None
        self.closed = False
        self.websocket_handler = None

    def set_default_timeout(self, timeout):
        self.timeout = timeout

    def new_page(self):
        return self.page

    def route_web_socket(self, _pattern, handler):
        self.websocket_handler = handler

    def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, context, *, contexts=None):
        self.context = context
        self.context_options = None
        self.closed = False
        # A remote Scraping Browser session may already have an open context;
        # an ISP-proxy-launched browser starts with none.
        self.contexts = contexts if contexts is not None else []

    def new_context(self, **kwargs):
        self.context_options = kwargs
        return self.context

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.launch_options = None
        self.cdp_endpoint = None
        self.cdp_timeout = None

    def launch(self, **kwargs):
        self.launch_options = kwargs
        return self.browser

    def connect_over_cdp(self, endpoint, **kwargs):
        self.cdp_endpoint = endpoint
        self.cdp_timeout = kwargs.get("timeout")
        return self.browser


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeManager:
    def __init__(self, playwright):
        self.playwright = playwright

    def start(self):
        return self.playwright


def fake_runtime(html="<html><div class='meeting'>Ready</div></html>"):
    page = FakePage(html)
    context = FakeBrowserContext(page)
    browser = FakeBrowser(context)
    playwright = FakePlaywright(FakeChromium(browser))
    return page, context, browser, playwright, lambda: FakeManager(playwright)


class BrowserContextTest(unittest.TestCase):
    def context(self, **browser_overrides):
        browser = {"enabled": True, **browser_overrides}
        proxy = {
            "server": "http://proxy.example:22225",
            "username": "browser-user",
            "password": "browser-password",
        }
        return RUNNER.Context({}, set(), {"browser": browser}, browser_proxy=proxy)

    def test_browser_get_is_opt_in_and_http_only(self):
        with self.assertRaises(RUNNER.BrowserRuntimeError):
            RUNNER.Context({}, set(), {}).browser_get("https://example.org")
        with self.assertRaises(ValueError):
            self.context().browser_get("file:///etc/passwd")
        with self.assertRaises(ValueError):
            self.context().browser_get("https://user:secret@example.org/")

    def test_rendered_response_uses_standard_ua_and_tears_down(self):
        page, browser_context, browser, playwright, loader = fake_runtime()
        ctx = self.context(timeout_seconds=9)
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            response = ctx.browser_get(
                "https://district.example/meetings", selector=".meeting"
            )
            ctx.close()
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ready", response.text)
        self.assertEqual(page.selector, ".meeting")
        self.assertEqual(browser_context.timeout, 9000)
        self.assertEqual(browser.context_options["user_agent"], RUNNER.USER_AGENT)
        self.assertEqual(playwright.chromium.launch_options["proxy"], {
            "server": "http://proxy.example:22225",
            "username": "browser-user",
            "password": "browser-password",
        })
        self.assertIsNone(ctx._browser_proxy)
        self.assertNotIn("ourschoolboard", browser.context_options["user_agent"].lower())
        self.assertFalse(browser.context_options["accept_downloads"])
        self.assertIsNotNone(browser_context.websocket_handler)
        self.assertTrue(page.closed)
        self.assertTrue(browser_context.closed)
        self.assertTrue(browser.closed)
        self.assertTrue(playwright.stopped)

    def test_browser_fails_closed_without_brightdata(self):
        ctx = RUNNER.Context({}, set(), {"browser": {"enabled": True}})
        with patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            with self.assertRaisesRegex(
                RUNNER.BrowserRuntimeError, "requires the Bright Data proxy"
            ):
                ctx.browser_get("https://district.example/meetings")

    def test_proxy_handoff_is_consumed_before_scraper_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "browser-proxy.json"
            path.write_text(json.dumps({
                "server": "brd.superproxy.io:22225",
                "username": "zone-user",
                "password": "zone-password",
            }), encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(os.environ, {
                "TMPDIR": directory,
                "SCRAPER_BROWSER_PROXY_FILE": str(path),
            }, clear=False):
                proxy = RUNNER._take_browser_proxy()
                self.assertNotIn("SCRAPER_BROWSER_PROXY_FILE", os.environ)
            self.assertFalse(path.exists())
        self.assertEqual(proxy, {
            "server": "http://brd.superproxy.io:22225",
            "username": "zone-user",
            "password": "zone-password",
        })

    def test_page_fetch_and_response_byte_limits_are_enforced(self):
        page, _context, _browser, _playwright, loader = fake_runtime("x" * 11)
        ctx = self.context(max_fetches=1, max_response_bytes=10)
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            with self.assertRaises(RUNNER.BrowserFetchError):
                ctx.browser_get("https://district.example/meetings")
        self.assertTrue(page.closed)

        page, _context, _browser, _playwright, loader = fake_runtime()
        ctx = self.context(max_fetches=1)
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            ctx.browser_get("https://district.example/meetings")
            with self.assertRaises(RUNNER.BrowserFetchError):
                ctx.browser_get("https://district.example/meetings?page=2")

    def test_browser_blocks_private_initial_and_subresource_urls(self):
        ctx = self.context()
        with patch.object(
            RUNNER, "assert_public_url",
            side_effect=RUNNER.UnsafeUrlError("non-public address"),
        ), self.assertRaises(RUNNER.UnsafeUrlError):
            ctx.browser_get("http://127.0.0.1/admin")

        page, context, browser, playwright, _loader = fake_runtime()
        page.request_url = "http://private.example/admin"
        loader = lambda: FakeManager(playwright)
        ctx = self.context()

        def validate(url):
            if "private.example" in url:
                raise RUNNER.UnsafeUrlError("non-public address")
            return url

        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=validate):
            with self.assertRaises(RUNNER.BrowserFetchError):
                ctx.browser_get("https://district.example/meetings")


class BrowserActionsTest(unittest.TestCase):
    """A source whose real content only appears after a click or a
    postback-driven control (not just after JS finishes running) needs more
    than navigate-and-read. ``actions`` is a bounded, declarative list the
    trusted runner executes itself — the scraper never gets a page handle."""

    def context(self, **browser_overrides):
        browser = {"enabled": True, **browser_overrides}
        proxy = {
            "server": "http://proxy.example:22225",
            "username": "browser-user",
            "password": "browser-password",
        }
        return RUNNER.Context({}, set(), {"browser": browser}, browser_proxy=proxy)

    def test_actions_execute_in_order_before_the_trailing_selector_wait(self):
        page, _context, _browser, _playwright, loader = fake_runtime()
        ctx = self.context()
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            ctx.browser_get(
                "https://district.example/meetings",
                selector=".meeting",
                actions=[
                    {"click": "a:has-text('Meetings')"},
                    {"fill": ["#search", "board minutes"]},
                    {"press": "Enter"},
                    {"wait_for_selector": ".results"},
                    {"wait_ms": 250},
                ],
            )
        self.assertEqual(page.clicks, ["a:has-text('Meetings')"])
        self.assertEqual(page.fills, [("#search", "board minutes")])
        self.assertEqual(page.keyboard.pressed, ["Enter"])
        # Two wait_for_selector calls happen: the action's own, then the
        # trailing ``selector`` parameter — both must run, in that order.
        self.assertEqual(page.selector, ".meeting")
        self.assertEqual(page.waited_ms, [250])

    def test_actions_default_to_empty_and_are_optional(self):
        page, _context, _browser, _playwright, loader = fake_runtime()
        ctx = self.context()
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            ctx.browser_get("https://district.example/meetings")
        self.assertEqual(page.clicks, [])
        self.assertEqual(page.fills, [])

    def test_actions_reject_unknown_type(self):
        with self.assertRaises(ValueError):
            self.context().browser_get(
                "https://district.example/meetings",
                actions=[{"double_click": "#thing"}],
            )

    def test_actions_reject_more_than_the_bound(self):
        too_many = [{"wait_ms": 1}] * (RUNNER.MAX_BROWSER_ACTIONS + 1)
        with self.assertRaises(ValueError):
            self.context().browser_get(
                "https://district.example/meetings", actions=too_many
            )

    def test_actions_reject_malformed_shapes(self):
        bad_action_lists = [
            [{"click": "", }],                       # empty selector
            [{"click": "x" * 501}],                   # selector too long
            [{"fill": ["#a"]}],                        # wrong arity
            [{"fill": ["#a", 5]}],                      # non-string value
            [{"wait_ms": 0}],                           # below minimum
            [{"wait_ms": RUNNER.MAX_BROWSER_ACTION_WAIT_MS + 1}],  # above max
            [{"wait_ms": True}],                        # bool is not an int
            [{"click": "#a", "press": "Enter"}],         # more than one key
            ["click"],                                    # not a mapping
        ]
        for actions in bad_action_lists:
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                self.context().browser_get(
                    "https://district.example/meetings", actions=actions
                )


class DirectBrowserProviderTest(unittest.TestCase):
    """Direct Chromium is explicit and retains the trusted browser boundary."""

    def context(self, **browser_overrides):
        browser = {"enabled": True, "provider": "direct", **browser_overrides}
        return RUNNER.Context({}, set(), {"browser": browser})

    def test_launches_local_chromium_without_proxy_credentials(self):
        page, browser_context, browser, playwright, loader = fake_runtime()
        ctx = self.context(timeout_seconds=15)
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            response = ctx.browser_get("https://district.example/meetings")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("proxy", playwright.chromium.launch_options)
        self.assertEqual(browser_context.timeout, 15000)
        self.assertFalse(browser.context_options["accept_downloads"])
        self.assertIsNotNone(browser_context.websocket_handler)

    def test_keeps_public_url_checks(self):
        ctx = self.context()
        with patch.object(
            RUNNER, "assert_public_url",
            side_effect=RUNNER.UnsafeUrlError("non-public address"),
        ), self.assertRaises(RUNNER.UnsafeUrlError):
            ctx.browser_get("http://127.0.0.1/admin")


class ScrapingBrowserProviderTest(unittest.TestCase):
    """The Scraping Browser is a different Bright Data product from the ISP
    proxy: a real, already-running remote Chromium reached over a single CDP
    URL, for sources that stay blocked (or need interaction) even through the
    ISP-proxied path. It is opt-in per scraper via config.browser.provider and
    never bypasses prohibiting terms on its own."""

    def context(self, endpoint="wss://zone-user:zone-pass@brd.superproxy.io:9222",
                **browser_overrides):
        browser = {"enabled": True, "provider": "scraping_browser", **browser_overrides}
        return RUNNER.Context(
            {}, set(), {"browser": browser},
            scraping_browser_endpoint=endpoint,
        )

    def test_connects_over_cdp_with_the_configured_endpoint(self):
        page, browser_context, browser, playwright, loader = fake_runtime()
        browser.contexts = [browser_context]
        ctx = self.context(timeout_seconds=15)
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            response = ctx.browser_get("https://district.example/meetings")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            playwright.chromium.cdp_endpoint,
            "wss://zone-user:zone-pass@brd.superproxy.io:9222",
        )
        self.assertEqual(playwright.chromium.cdp_timeout, 15000)
        # The already-open remote context is reused rather than opening a
        # second one on the same Scraping Browser session.
        self.assertIsNone(browser.context_options)
        self.assertIsNone(ctx._scraping_browser_endpoint)

    def test_creates_a_context_when_the_remote_browser_has_none_yet(self):
        page, browser_context, browser, _playwright, loader = fake_runtime()
        self.assertEqual(browser.contexts, [])
        ctx = self.context()
        with patch.object(RUNNER, "_load_playwright", return_value=loader), \
                patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            ctx.browser_get("https://district.example/meetings")
        self.assertIsNotNone(browser.context_options)

    def test_fails_closed_without_an_endpoint(self):
        ctx = RUNNER.Context(
            {}, set(), {"browser": {"enabled": True, "provider": "scraping_browser"}},
        )
        with patch.object(RUNNER, "assert_public_url", side_effect=lambda url: url):
            with self.assertRaisesRegex(
                RUNNER.BrowserRuntimeError, "requires the Scraping Browser endpoint"
            ):
                ctx.browser_get("https://district.example/meetings")

    def test_unknown_provider_is_rejected(self):
        ctx = RUNNER.Context(
            {}, set(), {"browser": {"enabled": True, "provider": "residential_pool"}},
        )
        with self.assertRaisesRegex(RuntimeError, "invalid config.browser.provider"):
            ctx.browser_get("https://district.example/meetings")

    def test_endpoint_handoff_is_consumed_before_scraper_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scraping-browser.json"
            path.write_text(json.dumps({
                "endpoint": "wss://zone-user:zone-pass@brd.superproxy.io:9222",
            }), encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(os.environ, {
                "TMPDIR": directory,
                "SCRAPER_SCRAPING_BROWSER_FILE": str(path),
            }, clear=False):
                endpoint = RUNNER._take_scraping_browser_endpoint()
                self.assertNotIn("SCRAPER_SCRAPING_BROWSER_FILE", os.environ)
            self.assertFalse(path.exists())
        self.assertEqual(endpoint, "wss://zone-user:zone-pass@brd.superproxy.io:9222")

    def test_endpoint_handoff_rejects_missing_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scraping-browser.json"
            path.write_text(json.dumps({"endpoint": "wss://brd.superproxy.io:9222"}),
                             encoding="utf-8")
            path.chmod(0o600)
            with patch.dict(os.environ, {
                "TMPDIR": directory,
                "SCRAPER_SCRAPING_BROWSER_FILE": str(path),
            }, clear=False):
                with self.assertRaises(RUNNER.BrowserRuntimeError):
                    RUNNER._take_scraping_browser_endpoint()


if __name__ == "__main__":
    unittest.main()
