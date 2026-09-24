import importlib.util
import json
import os
import stat
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("pipeline_sandbox_limits", PIPELINE / "sandbox.py")
SANDBOX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SANDBOX)


class SandboxLimitTest(unittest.TestCase):
    def test_explicit_document_filename_is_bounded_and_preserved(self):
        record = SANDBOX.validate({"document_url": "https://x.example/a", "extra": {"document_filename": "board-2026-08-24-978690.pdf"}})
        self.assertEqual("board-2026-08-24-978690.pdf", record["document_filename"])
        bad = SANDBOX.validate({"document_url": "https://x.example/a", "extra": {"document_filename": "../unsafe.pdf"}})
        self.assertIsNone(bad["document_filename"])
    def test_http_scraper_keeps_address_space_limit(self):
        with patch.object(SANDBOX.resource, "setrlimit") as set_limit:
            SANDBOX._limits(512)()
        resources = [call.args[0] for call in set_limit.call_args_list]
        self.assertIn(SANDBOX.resource.RLIMIT_AS, resources)

    def test_browser_uses_outer_cgroup_instead_of_incompatible_rlimit_as(self):
        with patch.object(SANDBOX.resource, "setrlimit") as set_limit:
            SANDBOX._limits(512, browser_enabled=True)()
        resources = [call.args[0] for call in set_limit.call_args_list]
        self.assertNotIn(SANDBOX.resource.RLIMIT_AS, resources)
        self.assertIn(SANDBOX.resource.RLIMIT_NPROC, resources)
        self.assertIn(SANDBOX.resource.RLIMIT_CORE, resources)

    def test_root_run_gives_only_child_scratch_to_sandbox_user(self):
        account = type("Account", (), {"pw_uid": 123, "pw_gid": 456})()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.object(SANDBOX.os, "geteuid", return_value=0), \
                 patch.object(SANDBOX.pwd, "getpwnam", return_value=account), \
                 patch.object(SANDBOX.os, "chown") as chown:
                child = SANDBOX._child_scratch(root)
        self.assertEqual(child.name, "child")
        chown.assert_called_once_with(child, 123, 456)

    def test_root_run_uses_separate_traversable_temp_base(self):
        with patch.object(SANDBOX.os, "geteuid", return_value=0), \
             patch.dict(SANDBOX.os.environ, {}, clear=True):
            self.assertEqual(
                SANDBOX._runtime_workdir(), Path("/tmp/ourschoolboard-scraper")
            )

    def test_browser_proxy_handoff_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "BRIGHTDATA_PROXY": "brd.superproxy.io:22225",
                "BRIGHTDATA_PROXY_USER": "zone-user",
                "BRIGHTDATA_PROXY_PASS": "zone-password",
            },
            clear=False,
        ), patch.object(SANDBOX.os, "geteuid", return_value=1000):
            path = SANDBOX._write_browser_proxy_handoff(Path(directory))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), {
                "server": "brd.superproxy.io:22225",
                "username": "zone-user",
                "password": "zone-password",
            })

    def test_missing_browser_proxy_credentials_do_not_create_handoff(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {}, clear=True
        ):
            self.assertIsNone(
                SANDBOX._write_browser_proxy_handoff(Path(directory))
            )

    def test_scraping_browser_handoff_embeds_credentials_in_one_endpoint(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "BRIGHTDATA_SCRAPING_BROWSER_USER": "zone-scraping-user",
                "BRIGHTDATA_WEB_ACCESS_PASS": "zone-scraping-password",
            },
            clear=False,
        ), patch.object(SANDBOX.os, "geteuid", return_value=1000):
            path = SANDBOX._write_scraping_browser_handoff(Path(directory))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(json.loads(path.read_text()), {
                "endpoint": (
                    "wss://zone-scraping-user:zone-scraping-password"
                    "@brd.superproxy.io:9222"
                ),
            })

    def test_scraping_browser_host_is_overridable(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "BRIGHTDATA_SCRAPING_BROWSER_HOST": "custom.example:9222",
                "BRIGHTDATA_SCRAPING_BROWSER_USER": "zone-scraping-user",
                "BRIGHTDATA_WEB_ACCESS_PASS": "zone-scraping-password",
            },
            clear=False,
        ), patch.object(SANDBOX.os, "geteuid", return_value=1000):
            path = SANDBOX._write_scraping_browser_handoff(Path(directory))
            self.assertEqual(json.loads(path.read_text())["endpoint"], (
                "wss://zone-scraping-user:zone-scraping-password@custom.example:9222"
            ))

    def test_missing_scraping_browser_credentials_do_not_create_handoff(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {}, clear=True
        ):
            self.assertIsNone(
                SANDBOX._write_scraping_browser_handoff(Path(directory))
            )


if __name__ == "__main__":
    unittest.main()
