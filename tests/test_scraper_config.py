import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "scripts" / "tools"
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path[:0] = [str(TOOLS), str(PIPELINE)]
SPEC = importlib.util.spec_from_file_location("scraper_store", TOOLS / "scraper_store.py")
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


class ScraperConfigTest(unittest.TestCase):
    def test_default_scraper_cadence_is_biweekly(self):
        self.assertEqual(STORE.DEFAULT_CADENCE_DAYS, 14)

    def test_bounded_reliability_overrides_are_accepted(self):
        STORE.validate_config({
            "document_max_bytes": 100 * 1024 * 1024,
            "timeout_seconds": 900,
            "stale_run_seconds": 7200,
        })

    def test_boolean_is_not_an_integer_override(self):
        with self.assertRaises(ValueError):
            STORE.validate_config({"timeout_seconds": True})

    def test_unbounded_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            STORE.validate_config({"timeout_seconds": 3600})

    def test_removed_pdf_fingerprint_flag_is_rejected(self):
        for value in (True, False, "yes"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                STORE.validate_config({"stable_pdf_fingerprint": value})

    def test_drive_view_resolver_flag_must_be_boolean(self):
        STORE.validate_config({"google_drive_view_pdf": True})
        with self.assertRaises(ValueError):
            STORE.validate_config({"google_drive_view_pdf": "yes"})

    def test_drive_media_resolver_flag_must_be_boolean(self):
        STORE.validate_config({"google_drive_view_media": True})
        with self.assertRaises(ValueError):
            STORE.validate_config({"google_drive_view_media": "yes"})

    def test_drive_media_resolver_requires_both_terms_findings(self):
        STORE.validate_drive_media_terms({
            "drive.google.com": "none_found",
            "drive.usercontent.google.com": "permissive",
        })
        with self.assertRaises(ValueError):
            STORE.validate_drive_media_terms({
                "drive.google.com": "none_found",
                "drive.usercontent.google.com": None,
            })

    def test_drive_view_resolver_requires_both_terms_findings(self):
        STORE.validate_drive_resolver_terms({
            "drive.google.com": "none_found",
            "googleusercontent.com": "permissive",
        })
        with self.assertRaises(ValueError):
            STORE.validate_drive_resolver_terms({
                "drive.google.com": "none_found",
                "googleusercontent.com": None,
            })
        with self.assertRaises(ValueError):
            STORE.validate_drive_resolver_terms({
                "drive.google.com": "prohibits",
                "googleusercontent.com": "none_found",
            })

    def test_youtube_transcript_config_is_bounded(self):
        STORE.validate_config({"youtube_transcripts": {
            "enabled": True, "languages": ["en", "es-419"],
        }})
        for value in (
            True,
            {"enabled": "yes"},
            {"enabled": True, "languages": []},
            {"enabled": True, "languages": ["not_a_language"]},
            {"enabled": True, "unknown": 1},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                STORE.validate_config({"youtube_transcripts": value})

    def test_granicus_caption_config_is_bounded(self):
        STORE.validate_config({"granicus_captions": {"enabled": True}})
        for value in (True, {"enabled": "yes"}, {"enabled": True, "unknown": 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                STORE.validate_config({"granicus_captions": value})

    def test_generic_video_transcript_config_is_bounded(self):
        STORE.validate_config({"video_transcripts": {
            "enabled": True,
            "model": "qwen/qwen3-asr-1.7b",
            "language": "en",
            "chunk_seconds": 300,
            "concurrency": 3,
            "google_drive": True,
        }, "video_max_bytes": 512 * 1024 * 1024})
        invalid = (
            True,
            {"enabled": "yes"},
            {"enabled": True, "model": "bad"},
            {"enabled": True, "fallback_models": ["qwen/qwen3-asr-0.6b"]},
            {"enabled": True, "language": "not_a_language"},
            {"enabled": True, "chunk_seconds": 10},
            {"enabled": True, "concurrency": 0},
            {"enabled": True, "concurrency": 7},
            {"enabled": True, "google_drive": "yes"},
            {"enabled": True, "unknown": 1},
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                STORE.validate_config({"video_transcripts": value})

    def test_drive_video_requires_both_terms_findings(self):
        STORE.validate_drive_video_terms({
            "drive.google.com": "none_found",
            "drive.usercontent.google.com": "permissive",
        })
        with self.assertRaises(ValueError):
            STORE.validate_drive_video_terms({
                "drive.google.com": "none_found",
                "drive.usercontent.google.com": None,
            })

    def test_bounded_browser_config_is_accepted(self):
        STORE.validate_config({"browser": {
            "enabled": True,
            "max_fetches": 8,
            "max_network_requests": 250,
            "timeout_seconds": 20,
            "max_response_bytes": 2 * 1024 * 1024,
        }})

    def test_browser_requires_explicit_boolean_opt_in(self):
        with self.assertRaises(ValueError):
            STORE.validate_config({"browser": {"enabled": "yes"}})

    def test_browser_hard_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            STORE.validate_config({"browser": {
                "enabled": True,
                "max_fetches": STORE.MAX_BROWSER_FETCHES + 1,
            }})

    def test_browser_provider_accepts_direct_and_brightdata_options(self):
        STORE.validate_config({"browser": {"enabled": True}})
        STORE.validate_config({"browser": {"enabled": True, "provider": "direct"}})
        STORE.validate_config({"browser": {"enabled": True, "provider": "isp_proxy"}})
        STORE.validate_config({"browser": {"enabled": True, "provider": "scraping_browser"}})

    def test_browser_provider_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            STORE.validate_config({"browser": {
                "enabled": True, "provider": "residential_pool",
            }})

    def test_browser_get_source_requires_browser_config(self):
        source = "def scrape(ctx):\n    ctx.browser_get('https://example.org')\n"
        with self.assertRaises(ValueError):
            STORE.validate_source(source, {})
        STORE.validate_source(source, {"browser": {"enabled": True}})

    def test_direct_playwright_import_is_forbidden(self):
        source = "import playwright\ndef scrape(ctx):\n    pass\n"
        with self.assertRaises(ValueError):
            STORE.validate_source(source, {"browser": {"enabled": True}})

    def test_scraper_version_is_allocated_across_the_whole_agency(self):
        class Cursor:
            def __init__(self):
                self.query = None
                self.params = None

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return {"version": 12}

        cursor = Cursor()
        self.assertEqual(STORE.next_scraper_version(cursor, 99), 12)
        self.assertIn("where agency_id=%s", cursor.query)
        self.assertNotIn("where district_id=%s", cursor.query)
        self.assertEqual(cursor.params, (99,))

    def test_pause_all_disables_only_enabled_schedules(self):
        class Cursor:
            def __init__(self):
                self.query = ""

            def execute(self, query):
                self.query = query

            def fetchall(self):
                return [{"district_id": 4}, {"district_id": 9}]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        cursor = Cursor()
        connection = type("Connection", (), {})()
        context = type("Context", (), {
            "__enter__": lambda self: connection,
            "__exit__": lambda self, *_args: False,
        })()
        with patch.object(STORE, "connect", return_value=context), \
                patch.object(STORE, "dict_cursor", return_value=cursor):
            result = STORE.pause_all()
        self.assertEqual(result["pausedSchedules"], 2)
        self.assertIn("set enabled=false", cursor.query)
        self.assertIn("where enabled", cursor.query)

    def test_pause_disables_only_the_named_district_schedule(self):
        class Cursor:
            def __init__(self):
                self.query = ""
                self.params = None

            def execute(self, query, params):
                self.query = query
                self.params = params

            def fetchone(self):
                return {"id": 17, "enabled": False}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        cursor = Cursor()
        connection = type("Connection", (), {})()
        context = type("Context", (), {
            "__enter__": lambda self: connection,
            "__exit__": lambda self, *_args: False,
        })()
        with patch.object(STORE, "connect", return_value=context), \
                patch.object(STORE, "dict_cursor", return_value=cursor):
            result = STORE.pause("southampton-ma")
        self.assertEqual(result["district"], "southampton-ma")
        self.assertEqual(result["schedule"]["enabled"], False)
        self.assertIn("d.slug=%s", cursor.query)
        self.assertEqual(cursor.params, ("southampton-ma",))

    def test_pause_command_dispatches_to_named_district_function(self):
        result = {"district": "southampton-ma", "schedule": {"enabled": False}}
        argv = [
            "scraper_store.py", "pause", "--district", "southampton-ma",
            "--confirm", "PAUSE southampton-ma",
        ]
        with patch.object(sys, "argv", argv), \
                patch.object(STORE, "pause", return_value=result) as pause_one, \
                patch("builtins.print"):
            STORE.main()
        pause_one.assert_called_once_with("southampton-ma")


if __name__ == "__main__":
    unittest.main()
