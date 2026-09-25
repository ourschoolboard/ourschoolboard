import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "page_store", ROOT / "scripts" / "tools" / "page_store.py"
)
PAGE_STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PAGE_STORE)


def complete_feed():
    return {
        "district": {"id": "example-nj", "name": "Example Schools"},
        "period": {"start": "2025-08-11", "end": "2026-08-11"},
        "summary": {"documentsRead": 45, "alertsPublished": 5},
        "categories": [{"id": "budget", "label": "Financial", "count": 1}],
        "limitations": [],
    }


def complete_alert():
    return {
        "id": "example-budget",
        "title": "Board adopts budget reductions",
        "summary": "The board adopted the revised budget.",
        "whyItMatters": "The reductions affect staffing next year.",
        "evidence": "Approved minutes record the motion and vote.",
        "categoryLabel": "Financial",
        "topic": "budget",
        "severity": "high",
        "schoolScope": ["Example Middle School"],
        "date": "2026-06-08",
        "sourceUrl": "https://district.example/minutes.pdf",
        "sourceTitle": "June 8, 2026 approved minutes",
        "sourceHost": "district.example",
        "sourceType": "Board meeting minutes",
        "status": "published",
    }


def complete_overview():
    return {
        "district": {"id": "example-nj", "name": "Example Schools"},
        "keyStructure": {
            "navLabel": "Funding", "heading": "How is the district funded?",
            "status": "Formula-funded district",
            "explanation": "State aid and local revenue support one district budget.",
            "points": [
                {"point": "One board", "detail": "Five elected members govern the district."},
                {"point": "One budget", "detail": "The board adopts the annual request."},
                {"point": "Local approval", "detail": "The municipality sets the final appropriation."},
            ],
        },
        "metrics": [{"id": "schools", "label": "District schools", "value": 7, "unit": "schools"}],
        "projects": [{
            "id": "budget", "name": "FY27 budget", "status": "in_progress",
            "summary": "The district is implementing the adopted budget.",
            "timeline": [{"date": "2026-06-01", "label": "Board adopts budget"}],
            "budget": {"fundingSource": "Local appropriation and state aid"},
            "nextMilestone": "Publish the first quarterly report",
        }],
        "method": "Official district records and the NCES directory.",
        "limitations": [],
    }


def complete_category_page():
    return {
        "topic": "facilities",
        "categoryLabel": "Facilities",
        "narrative": "The district has approved several facilities projects this year.",
        "highlights": [{
            "alertId": "42", "title": "Roof replacement approved",
            "summary": "The board approved a roof replacement at the middle school.",
        }],
        "method": "Synthesized from 3 published, source-verified alerts about facilities.",
        "limitations": [],
    }


class PageStoreValidationTests(unittest.TestCase):
    def test_source_identity_migration_requires_matching_drive_file_ids(self):
        source = {
            "currentUrl": "https://drive.google.com/uc?export=download&id=Abc_123-Xyz98765",
            "canonicalUrl": "https://drive.google.com/file/d/Abc_123-Xyz98765/view",
            "sha256": "a" * 64,
            "storageKey": "scrapes/example/document.pdf",
            "title": "Board agenda",
            "kind": "agenda",
        }
        items, digest = PAGE_STORE.normalize_source_identity_migration({"sources": [source]})
        self.assertEqual(items[0]["canonicalUrl"], source["canonicalUrl"])
        self.assertEqual(len(digest), 64)
        source["canonicalUrl"] = "https://drive.google.com/file/d/Other_123-Xyz987/view"
        with self.assertRaisesRegex(ValueError, "Drive file IDs must match"):
            PAGE_STORE.normalize_source_identity_migration({"sources": [source]})

    def test_page_image_normalizes_licensed_attribution(self):
        image = PAGE_STORE.normalize_page_image({
            "id": "commons:File:School.jpg",
            "url": "https://upload.wikimedia.org/school.jpg",
            "alt": "A public school building",
            "attribution": "Example Photographer",
            "attributionUrl": "https://commons.wikimedia.org/wiki/File:School.jpg",
            "license": "BY-SA",
            "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0/",
            "width": 1200,
            "height": 800,
        })
        self.assertEqual(image["license"], "by-sa")

    def test_page_image_rejects_unlicensed_or_incomplete_images(self):
        with self.assertRaisesRegex(ValueError, "requires attribution"):
            PAGE_STORE.normalize_page_image({
                "id": "openverse:1", "url": "https://example.org/image.jpg",
                "alt": "School",
            })
        with self.assertRaisesRegex(ValueError, "license must be one of"):
            PAGE_STORE.normalize_page_image({
                "id": "x", "url": "https://example.org/image.jpg", "alt": "School",
                "attribution": "Creator", "attributionUrl": "https://example.org/work",
                "license": "all-rights-reserved",
            })

    def test_source_evidence_requires_complete_editorial_provenance(self):
        source_image = {
            "id": "evidence:1", "url": "https://bucket.example/evidence.webp",
            "alt": "Excerpt from the board packet", "attribution": "Official source excerpt",
            "attributionUrl": "https://district.example/packet.pdf",
            "license": "source-evidence", "licenseUrl": "https://district.example/packet.pdf",
            "editorial": {
                "kind": "source-evidence", "renderer": "pillow-caption-panel",
                "version": "v1", "baseImageId": "source:1:abc", "label": "Budget update",
                "visualSha256": "a" * 64,
            },
        }
        self.assertEqual(
            PAGE_STORE.normalize_page_image(source_image)["license"], "source-evidence"
        )
        source_image.pop("editorial")
        with self.assertRaisesRegex(ValueError, "requires source-evidence provenance"):
            PAGE_STORE.normalize_page_image(source_image)

    def test_same_fallback_base_cannot_be_reused_within_a_district(self):
        class Cursor:
            def __init__(self):
                self.rows = iter([None, None, {"label": "prior-alert"}])
            def execute(self, *_args):
                return None
            def fetchone(self):
                return next(self.rows)

        image = {
            "id": "editorial:new", "url": "https://example.org/new.webp",
            "editorial": {"baseImageId": "commons:File:Shared.jpg"},
        }
        with self.assertRaisesRegex(ValueError, "fallback background is already used"):
            PAGE_STORE.assert_image_unused(
                Cursor(), image, district_id=7, alert_key="new-alert"
            )

    def test_board_directory_accepts_camel_and_snake_case_fields(self):
        members = PAGE_STORE.normalize_board_members([{
            "name": "Alex Rivera", "title": "Board President",
            "trustee_area": "Area 2", "email": "alex@example.org",
            "term_ends": "2028", "source_url": "https://district.example/board",
        }])
        self.assertEqual(members[0]["role"], "Board President")
        self.assertEqual(members[0]["trusteeArea"], "Area 2")
        contact = PAGE_STORE.normalize_board_contact({
            "email_addresses": ["board@example.org", "BOARD@example.org"],
            "mailing_address": "1 School Way", "contact_url": "https://district.example/contact",
        })
        self.assertEqual(contact["emails"], ["board@example.org"])

    def test_board_directory_rejects_invalid_shapes(self):
        with self.assertRaisesRegex(ValueError, "must be an array"):
            PAGE_STORE.normalize_board_members({})
        with self.assertRaisesRegex(ValueError, "name is required"):
            PAGE_STORE.normalize_board_members([{"role": "Trustee"}])
        with self.assertRaisesRegex(ValueError, "email address"):
            PAGE_STORE.normalize_board_contact({"emails": ["invalid"]})

    def test_source_list_limit_is_bounded(self):
        self.assertEqual(PAGE_STORE.require_source_limit(5000), 5000)
        for value in (0, 10_001, True, "5"):
            with self.assertRaisesRegex(ValueError, "source limit"):
                PAGE_STORE.require_source_limit(value)

    def test_drive_reconciliation_manifest_is_bounded_and_identity_checked(self):
        manifest = {"logicalSources": [{
            "canonicalUrl": "https://drive.google.com/file/d/abc123/view",
            "currentBinaryUrl": (
                "https://drive.usercontent.google.com/download?id=abc123&export=download&confirm=t"
            ),
            "expectedBinarySha256": "a" * 64,
            "expectedBinaryStorageKey": f"sources/aa/{'a' * 64}",
            "title": "SSC minutes", "kind": "minutes", "meetingDate": "2026-01-12",
        }]}
        items, digest = PAGE_STORE.normalize_drive_reconciliation(manifest)
        self.assertEqual(items[0]["driveId"], "abc123")
        self.assertEqual(len(digest), 64)
        mismatched = {"logicalSources": [{
            **manifest["logicalSources"][0],
            "currentBinaryUrl": (
                "https://drive.usercontent.google.com/download?id=other&export=download&confirm=t"
            ),
        }]}
        with self.assertRaisesRegex(ValueError, "matching Drive download URL"):
            PAGE_STORE.normalize_drive_reconciliation(mismatched)

    def test_drive_reconciliation_shell_only_requires_expected_bytes(self):
        manifest = {"logicalSources": [{
            "canonicalUrl": "https://drive.google.com/file/d/abc123/view",
            "dropShellOnly": True, "title": "Empty shell", "kind": "other",
            "meetingDate": None,
        }]}
        with self.assertRaisesRegex(ValueError, "expectedShellSha256"):
            PAGE_STORE.normalize_drive_reconciliation(manifest)

    def test_published_feed_requires_structured_shape(self):
        PAGE_STORE.validate_feed_content(complete_feed())
        payload = complete_feed()
        payload["summary"] = "Five alerts from the latest records."
        with self.assertRaisesRegex(ValueError, "structured summary"):
            PAGE_STORE.validate_feed_content(payload)

    def test_published_feed_rejects_boolean_summary_count(self):
        payload = complete_feed()
        payload["summary"] = {"documentsRead": True}
        with self.assertRaisesRegex(ValueError, "nonnegative integer count"):
            PAGE_STORE.validate_feed_content(payload)

    def test_published_overview_requires_editorial_sections(self):
        PAGE_STORE.validate_overview_content(complete_overview())
        for field, message in (("metrics", "metric"), ("projects", "project")):
            payload = complete_overview()
            payload[field] = []
            with self.assertRaisesRegex(ValueError, message):
                PAGE_STORE.validate_overview_content(payload)

    def test_published_category_page_requires_structured_shape(self):
        PAGE_STORE.validate_category_content(complete_category_page())
        payload = complete_category_page()
        payload["topic"] = "sports"
        with self.assertRaisesRegex(ValueError, "topic"):
            PAGE_STORE.validate_category_content(payload)
        payload = complete_category_page()
        payload["highlights"] = []
        with self.assertRaisesRegex(ValueError, "at least one highlight"):
            PAGE_STORE.validate_category_content(payload)
        payload = complete_category_page()
        del payload["highlights"][0]["alertId"]
        with self.assertRaisesRegex(ValueError, "alertId"):
            PAGE_STORE.validate_category_content(payload)

    def test_put_category_page_rejects_malformed_alert_ids(self):
        with patch.dict(os.environ, {"APP_ENV": "staging"}), patch.object(PAGE_STORE, "load_env"):
            with self.assertRaisesRegex(ValueError, "source_alert_ids"):
                PAGE_STORE.put_category_page(
                    "example-nj", "facilities", complete_category_page(), "draft", 3, ["42"]
                )
            with self.assertRaisesRegex(ValueError, "alert_count"):
                PAGE_STORE.put_category_page(
                    "example-nj", "facilities", complete_category_page(), "draft", -1, [42]
                )
            with self.assertRaisesRegex(ValueError, "topic must be one of"):
                PAGE_STORE.put_category_page(
                    "example-nj", "sports", complete_category_page(), "draft", 3, [42]
                )

    def test_put_functions_refuse_to_run_outside_staging(self):
        with patch.dict(os.environ, {"APP_ENV": "production"}), patch.object(PAGE_STORE, "load_env"):
            with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                PAGE_STORE.put_category_page(
                    "example-nj", "facilities", complete_category_page(), "draft", 3, [42]
                )
            with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                PAGE_STORE.put_page("example-nj", "feed", complete_feed(), "draft")
            with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                PAGE_STORE.put_alerts("example-nj", [complete_alert()])
            with self.assertRaisesRegex(ValueError, "refusing to write outside staging"):
                PAGE_STORE.put_district({"slug": "example-nj", "name": "Example Schools"})

    def test_reviewed_alert_writer_requires_explicit_production_flag(self):
        with patch.object(PAGE_STORE, "load_env"):
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                with self.assertRaisesRegex(ValueError, "ALERT_REVIEW_ENABLED=true"):
                    PAGE_STORE.require_alert_review_env()
            with patch.dict(os.environ, {
                "APP_ENV": "production", "ALERT_REVIEW_ENABLED": "true"
            }, clear=True):
                PAGE_STORE.require_alert_review_env()

    def test_reviewed_alert_writer_uses_alert_keys_and_durable_state(self):
        source = (ROOT / "scripts" / "tools" / "page_store.py").read_text()
        self.assertIn("select alert_key,status", source)
        self.assertIn("alert_review_status='notify_pending'", source)
        self.assertIn("alert_review_alert_ids=%s", source)

    def test_published_overview_rejects_scraping_narrative_in_core(self):
        payload = complete_overview()
        payload["keyStructure"]["explanation"] = "The scraper collected a repeatable corpus."
        with self.assertRaisesRegex(ValueError, "not scraping operations"):
            PAGE_STORE.validate_overview_content(payload)

    def test_published_overview_requires_project_timeline_and_funding(self):
        payload = complete_overview()
        payload["projects"][0]["timeline"] = []
        with self.assertRaisesRegex(ValueError, "timeline"):
            PAGE_STORE.validate_overview_content(payload)
        payload = complete_overview()
        del payload["projects"][0]["budget"]
        with self.assertRaisesRegex(ValueError, "funding context"):
            PAGE_STORE.validate_overview_content(payload)

    def test_published_alert_requires_every_detail_section(self):
        PAGE_STORE.normalize_alert(complete_alert())
        payload = complete_alert()
        del payload["whyItMatters"]
        with self.assertRaisesRegex(ValueError, "whyItMatters"):
            PAGE_STORE.normalize_alert(payload)

    def test_published_alert_source_host_must_match_url(self):
        payload = complete_alert()
        payload["sourceHost"] = "other.example"
        with self.assertRaisesRegex(ValueError, "sourceHost must match"):
            PAGE_STORE.normalize_alert(payload)

    def test_published_alert_requires_normalized_school_scope(self):
        payload = complete_alert()
        payload["schoolScope"] = [" Example Middle School ", "example middle school"]
        self.assertEqual(
            PAGE_STORE.normalize_alert(payload)["schoolScope"],
            ["Example Middle School"],
        )
        del payload["schoolScope"]
        with self.assertRaisesRegex(ValueError, "schoolScope is required"):
            PAGE_STORE.normalize_alert(payload)

    def test_all_schools_scope_cannot_be_mixed_with_school_names(self):
        payload = complete_alert()
        payload["schoolScope"] = ["all schools", "Example Middle School"]
        with self.assertRaisesRegex(ValueError, "must not mix"):
            PAGE_STORE.normalize_alert(payload)

    def test_grade_level_scopes_are_canonical_and_composable(self):
        payload = complete_alert()
        payload["schoolScope"] = [" ALL_MIDDLE ", "all_high", "Example K-8 School"]
        self.assertEqual(
            PAGE_STORE.normalize_alert(payload)["schoolScope"],
            ["all_middle", "all_high", "Example K-8 School"],
        )

    def test_curriculum_is_canonical_and_unknown_topics_are_rejected(self):
        payload = complete_alert()
        payload["topic"] = "curriculum"
        payload["categoryLabel"] = "Curriculum"
        self.assertEqual(PAGE_STORE.normalize_alert(payload)["topic"], "curriculum")
        payload["topic"] = "academics"
        with self.assertRaisesRegex(ValueError, "topic must be one of"):
            PAGE_STORE.normalize_alert(payload)

    def test_published_alert_requires_a_collected_source(self):
        payload = PAGE_STORE.normalize_alert(complete_alert())
        with self.assertRaisesRegex(ValueError, "does not resolve to a collected sourceUrl"):
            PAGE_STORE.source_backed_alert_content(payload, None)
        content = PAGE_STORE.source_backed_alert_content(payload, {"id": 123})
        self.assertEqual(content["sourceUrl"], payload["sourceUrl"])
        self.assertNotIn("verifiedPhrases", content)

    def test_legacy_needles_are_ignored(self):
        payload = PAGE_STORE.normalize_alert({
            **complete_alert(),
            "needles": ["text that no longer has to match"],
            "_sourceFile": "/tmp/old-source.pdf",
            "_sourceTextFile": "/tmp/old-source.txt",
        })
        content = PAGE_STORE.source_backed_alert_content(payload, {"id": 123})
        self.assertNotIn("needles", content)
        self.assertNotIn("_sourceFile", content)

    def test_source_detachment_manifest_is_normalized_and_hashed(self):
        item = {
            "url": "https://district.example/non-board-video",
            "sha256": "a" * 64,
            "storageKey": "sources/aa/example.txt",
            "reason": "Official-channel video is not a board meeting.",
        }
        items, digest = PAGE_STORE.normalize_source_detachment({"sources": [item]})
        self.assertEqual(items, [item])
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_source_detachment_requires_exact_provenance(self):
        base = {
            "url": "https://district.example/non-board-video",
            "sha256": "a" * 64,
            "storageKey": "sources/aa/example.txt",
            "reason": "Not a board meeting.",
        }
        with self.assertRaisesRegex(ValueError, "sha256"):
            PAGE_STORE.normalize_source_detachment({"sources": [{**base, "sha256": "bad"}]})
        with self.assertRaisesRegex(ValueError, "must be unique"):
            PAGE_STORE.normalize_source_detachment({"sources": [base, base]})


if __name__ == "__main__":
    unittest.main()


class DistrictIdentityTest(unittest.TestCase):
    """The NCES id is the identity; the slug is only a URL.

    Re-onboarding under a different slug convention used to insert a second
    district rather than update the first, leaving the alerts on one row and
    the new page content on the other.
    """

    def test_lookup_by_nces_before_insert(self):
        source = (ROOT / "scripts" / "tools" / "page_store.py").read_text(encoding="utf-8")
        self.assertIn("where nces_id=%s and slug<>%s", source)
        self.assertIn("updating it instead of creating", source)

    def test_migration_enforces_one_row_per_nces_id(self):
        migration = (ROOT / "migrations" / "0002_onboarding_storage.sql").read_text(encoding="utf-8")
        self.assertIn("create unique index if not exists {{p}}districts_nces_id_key", migration)
        self.assertIn("where nces_id is not null and nces_id <> ''", migration)
