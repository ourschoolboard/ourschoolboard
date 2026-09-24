import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "past_report_store", ROOT / "scripts" / "tools" / "past_report_store.py"
)
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


def complete_report():
    return {
        "reportKey": "2026-08-12-board-meeting",
        "reportDate": "2026-08-12",
        "reportType": "board_meeting",
        "title": "August 12 board meeting",
        "summary": "The board reviewed staffing and the proposed budget.",
        "schoolScope": ["all schools"],
        "documentLinks": [
            {
                "url": "https://district.example/meetings/2026-08-12",
                "title": "Meeting page",
                "sourceType": "official meeting page",
            },
            {
                "storageKey": "sources/ab/abcdef.pdf",
                "title": "Agenda packet",
                "contentType": "application/pdf",
            },
        ],
        "status": "published",
    }


class LinkCursor:
    def __init__(self, results):
        self.results = list(results)
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def execute(self, _query, params):
        self.params.append(params)

    def fetchall(self):
        return [(key,) for key in self.results.pop(0)]


class LinkConnection:
    def __init__(self, *results):
        self.cursor_instance = LinkCursor(results)

    def cursor(self):
        return self.cursor_instance


class PastReportStoreTests(unittest.TestCase):
    def test_writes_require_staging(self):
        with patch.object(STORE, "load_env"):
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                    STORE.require_staging_env()
            with patch.dict(os.environ, {"APP_ENV": "staging"}, clear=True):
                STORE.require_staging_env()

    def test_normalizes_supported_report_and_link_shapes(self):
        report = STORE.normalize_report(complete_report())
        self.assertEqual(report["report_type"], "board_meeting")
        self.assertEqual(report["school_scope"], ["all schools"])
        self.assertEqual(report["document_links"][0]["sourceType"], "official meeting page")
        self.assertEqual(report["document_links"][1]["storageKey"], "sources/ab/abcdef.pdf")

    def test_rejects_unknown_report_type_and_invalid_date(self):
        payload = complete_report()
        payload["reportType"] = "newsletter"
        with self.assertRaisesRegex(ValueError, "reportType must be one of"):
            STORE.normalize_report(payload)
        payload = complete_report()
        payload["reportDate"] = "not-a-date"
        with self.assertRaises(ValueError):
            STORE.normalize_report(payload)

    def test_allows_unknown_official_date(self):
        payload = complete_report()
        payload["reportDate"] = None
        self.assertIsNone(STORE.normalize_report(payload)["report_date"])

    def test_published_report_requires_school_scope(self):
        payload = complete_report()
        del payload["schoolScope"]
        with self.assertRaisesRegex(ValueError, "schoolScope is required"):
            STORE.normalize_report(payload)

    def test_requires_title_summary_and_stable_key(self):
        for field in ("title", "summary", "reportKey"):
            payload = complete_report()
            payload[field] = ""
            with self.assertRaises(ValueError):
                STORE.normalize_report(payload)
        payload = complete_report()
        payload["reportKey"] = "Spaces are not allowed"
        with self.assertRaisesRegex(ValueError, "lowercase words"):
            STORE.normalize_report(payload)

    def test_links_are_strict_and_require_exactly_one_reference(self):
        invalid = [
            {"title": "No reference"},
            {"url": "https://district.example/a", "storageKey": "sources/a/b.pdf"},
            {"url": "ftp://district.example/a"},
            {"url": "https://district.example/a", "extra": "not allowed"},
            {"storageKey": "other-bucket/file.pdf"},
            {"url": "https://district.example/a"},
        ]
        for link in invalid:
            with self.subTest(link=link), self.assertRaises(ValueError):
                STORE.normalize_document_links([link])

    def test_links_must_belong_to_selected_district(self):
        links = [
            {"url": "https://district.example/agenda.pdf"},
            {"storageKey": "sources/ab/abcdef.pdf"},
        ]
        conn = LinkConnection(
            {"https://district.example/agenda.pdf"}, {"sources/ab/abcdef.pdf"}
        )
        STORE.verify_document_links(conn, 42, links)
        self.assertEqual(conn.cursor_instance.params, [
            (42, ["https://district.example/agenda.pdf"]),
            (42, ["sources/ab/abcdef.pdf"], 42, ["sources/ab/abcdef.pdf"]),
        ])

        with self.assertRaisesRegex(ValueError, "url is not collected"):
            STORE.verify_document_links(LinkConnection(set(), set()), 42, links)
        with self.assertRaisesRegex(ValueError, "storageKey is not collected"):
            STORE.verify_document_links(
                LinkConnection({"https://district.example/agenda.pdf"}, set()), 42, links
            )

    def test_selector_accepts_stable_key_or_numeric_id(self):
        self.assertEqual(STORE.selector(None, "2026-08-board-meeting"),
                         (None, "2026-08-board-meeting"))
        self.assertEqual(STORE.selector(7, None), (7, None))
        for values in ((None, None), (7, "2026-08-board-meeting")):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                STORE.selector(*values)

    def test_limits_are_bounded_and_ids_positive(self):
        self.assertEqual(STORE.require_limit(500), 500)
        self.assertEqual(STORE.positive_id("7"), 7)
        for value in (0, 1001, True, "5"):
            with self.assertRaises(ValueError):
                STORE.require_limit(value)
        with self.assertRaises(ValueError):
            STORE.positive_id("0")


if __name__ == "__main__":
    unittest.main()
