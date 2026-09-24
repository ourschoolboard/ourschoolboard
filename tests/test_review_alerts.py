import importlib.util
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("review_alerts", ROOT / "scripts/pipeline/review_alerts.py")
REVIEW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REVIEW)


class ReviewAlertBatchingTests(unittest.TestCase):
    def test_model_tools_enforce_strict_object_schemas(self):
        def assert_closed_objects(schema):
            if schema.get("type") == "object":
                self.assertFalse(schema.get("additionalProperties", True))
                for child in schema.get("properties", {}).values():
                    assert_closed_objects(child)
            if schema.get("type") == "array":
                assert_closed_objects(schema["items"])

        for tool in REVIEW.MODEL_TOOLS:
            self.assertIs(tool.get("strict"), True)
            assert_closed_objects(tool["input_schema"])

    def test_review_model_requires_evidence_backed_school_scope(self):
        alert_schema = REVIEW.TOOL["input_schema"]["properties"]["alerts"]["items"]
        self.assertIn("schoolScope", alert_schema["required"])
        self.assertIn('cases where you are absolutely unsure what school is affected', REVIEW.SYSTEM)
        self.assertIn('"all_elementary", "all_middle", or "all_high"', REVIEW.SYSTEM)

    def test_batches_keep_same_date_documents_together(self):
        docs = [
            {"id": 1, "meeting_date": "2026-01-01", "text": "a" * 70},
            {"id": 2, "meeting_date": "2026-01-01", "text": "b" * 70},
            {"id": 3, "meeting_date": "2026-02-01", "text": "c" * 70},
        ]
        batches = REVIEW.batch_documents(docs, limit=150)
        self.assertEqual([[1, 2], [3]], [[d["id"] for d in batch] for batch in batches])

    def test_oversized_same_date_group_is_split_at_document_boundaries(self):
        docs = [
            {"id": 1, "meeting_date": "2026-01-01", "text": "a" * 70},
            {"id": 2, "meeting_date": "2026-01-01", "text": "b" * 70},
            {"id": 3, "meeting_date": "2026-01-01", "text": "c" * 70},
        ]
        batches = REVIEW.batch_documents(docs, limit=150)
        self.assertEqual([[1, 2], [3]], [[d["id"] for d in batch] for batch in batches])

    def test_initial_document_catalog_is_a_compact_preview(self):
        catalog = REVIEW.model_document_catalog([{
            "id": 1, "meeting_date": "2026-01-01", "kind": "minutes",
            "title": "Long minutes", "text": "x" * 100_000,
        }])

        self.assertNotIn("text", catalog[0])
        self.assertEqual(100_000, catalog[0]["characters"])
        self.assertEqual(REVIEW.INITIAL_DOCUMENT_PREVIEW_CHARS, len(catalog[0]["preview"]))
        self.assertLess(len(json.dumps(catalog)), 2_000)

    def test_minutes_outcome_replaces_duplicate_agenda_proposal(self):
        docs = {
            "1": {"kind": "agenda", "meeting_date": "2026-05-26"},
            "2": {"kind": "minutes", "meeting_date": "2026-05-26"},
        }
        candidates = [
            {"documentId": "1", "topic": "budget", "title": "Board to consider $100M Measure B bond issuance", "summary": "Proposed issuance"},
            {"documentId": "2", "topic": "budget", "title": "Board approves $100M Measure B bond issuance", "summary": "Approved issuance"},
        ]
        result = REVIEW.dedupe_candidates(candidates, docs)
        self.assertEqual(["2"], [item["documentId"] for item in result])

    def test_undated_document_candidate_is_not_published(self):
        candidate = {
            "documentId": "1", "slug": "budget-update", "topic": "budget",
            "title": "Budget update", "summary": "Summary",
            "whyItMatters": "Why", "evidence": "Evidence",
            "schoolScope": ["all schools"], "severity": "medium",
        }
        documents = {"1": {
            "meeting_date": None, "kind": "publication", "title": "Budget update",
            "source_url": "https://district.example/budget.pdf",
        }}

        alerts = REVIEW.build_alerts({"slug": "district"}, [candidate], documents)

        self.assertEqual([], alerts)

    def test_dated_document_candidate_keeps_source_date(self):
        candidate = {
            "documentId": "1", "slug": "budget-update", "topic": "budget",
            "title": "Budget update", "summary": "Summary",
            "whyItMatters": "Why", "evidence": "Evidence",
            "schoolScope": ["all schools"], "severity": "medium",
        }
        documents = {"1": {
            "meeting_date": "2026-08-20", "kind": "publication", "title": "Budget update",
            "source_url": "https://district.example/budget.pdf",
        }}

        alerts = REVIEW.build_alerts({"slug": "district"}, [candidate], documents)

        self.assertEqual("2026-08-20", alerts[0]["date"])
        self.assertEqual("district-2026-08-20-budget-update", alerts[0]["id"])


class ModelContextToolTests(unittest.TestCase):
    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def test_model_reads_past_alerts_and_overview_before_publishing(self):
        responses = [
            self.Response({
                "id": "response-1", "output": [{"type": "function_call", "call_id": "past-1",
                             "name": "list_past_alerts", "arguments": "{}"}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }),
            self.Response({
                "id": "response-2", "output": [{"type": "function_call", "call_id": "overview-1",
                             "name": "get_school_overview", "arguments": "{}"}],
                "usage": {"input_tokens": 12, "output_tokens": 3},
            }),
            self.Response({
                "id": "response-3", "output": [{"type": "function_call", "call_id": "publish-1",
                             "name": "publish_alert_candidates", "arguments": json.dumps({
                                 "alerts": [{"documentId": "5", "title": "New item"}]
                             })}],
                "usage": {"input_tokens": 14, "output_tokens": 4},
            }),
        ]
        target = {
            "name": "Example Schools",
            "started_at": datetime(2026, 8, 20, 8, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 8, 20, 8, 5, tzinfo=timezone.utc),
            "previous_scrape_at": datetime(2026, 8, 13, 8, 5, tzinfo=timezone.utc),
            "past_alerts": [{"title": "Existing budget alert", "event_date": "2026-08-01"}],
            "overview": {"district": {"name": "Example Schools"}},
        }
        documents = [{
            "id": 5, "meeting_date": "2026-08-19", "kind": "minutes",
            "title": "Minutes", "text": "Board approved a consequential item.",
        }]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(
            REVIEW, "current_date_utc", return_value="2026-08-20"
        ), patch.object(REVIEW.requests, "post", side_effect=responses) as post:
            alerts, usage = REVIEW.call_model(target, documents)

        self.assertEqual([{"documentId": "5", "title": "New item"}], alerts)
        self.assertEqual({"input_tokens": 36, "output_tokens": 9}, usage)
        self.assertEqual(
            {"type": "function", "name": "list_past_alerts"},
            post.call_args_list[0].kwargs["json"]["tool_choice"],
        )
        first_payload = post.call_args_list[0].kwargs["json"]
        first_context = json.loads(first_payload["input"])
        self.assertEqual("2026-08-20", first_context["currentDateUtc"])
        self.assertEqual("2026-08-20T08:05:00+00:00", first_context["scrapeFinishedAt"])
        self.assertEqual("2026-08-13T08:05:00+00:00", first_context["previousSuccessfulScrapeAt"])
        second_payload = post.call_args_list[1].kwargs["json"]
        self.assertEqual("response-1", second_payload["previous_response_id"])
        self.assertIn("Existing budget alert", second_payload["input"][0]["output"])
        third_payload = post.call_args_list[2].kwargs["json"]
        self.assertEqual("response-2", third_payload["previous_response_id"])
        self.assertIn("Example Schools", third_payload["input"][0]["output"])
        self.assertEqual("gpt-6-luna", first_payload["model"])
        self.assertEqual({"effort": "medium"}, first_payload["reasoning"])
        self.assertEqual("https://api.openai.com/v1/responses", post.call_args_list[0].args[0])

    def test_malformed_individual_candidates_are_discarded(self):
        responses = [
            self.Response({"id": "response-1", "output": [{"type": "function_call", "call_id": "past-1",
                                        "name": "list_past_alerts", "arguments": "{}"}]}),
            self.Response({"id": "response-2", "output": [{"type": "function_call", "call_id": "publish-1",
                                        "name": "publish_alert_candidates", "arguments": json.dumps({
                                            "alerts": ["bad", {"documentId": "1"}]
                                        })}]}),
        ]
        target = {"name": "Example", "past_alerts": [], "overview": None}
        documents = [{"id": 1, "meeting_date": "2026-08-20", "kind": "minutes",
                      "title": "Minutes", "text": "text"}]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(
            REVIEW.requests, "post", side_effect=responses
        ):
            alerts, _usage = REVIEW.call_model(target, documents)

        self.assertEqual([{"documentId": "1"}], alerts)

    def test_search_and_document_read_tools_are_available(self):
        names = {tool["name"] for tool in REVIEW.MODEL_TOOLS}
        self.assertIn("get_alert_detail", names)
        self.assertIn("search_past_documents", names)
        self.assertIn("read_document_content", names)
        self.assertIn("complete extracted bodies", REVIEW.SEARCH_PAST_DOCUMENTS_TOOL["description"])
        self.assertIn("no more than fifteen", REVIEW.SYSTEM)
        self.assertIn("consequences without their underlying", REVIEW.SYSTEM)
        self.assertIn("Do not alert on routine facilities maintenance", REVIEW.SYSTEM)
        self.assertIn("including\nfire-alarm replacements", REVIEW.SYSTEM)
        self.assertIn("establishes an emergency", REVIEW.SYSTEM)
        self.assertIn("summary to two concise sentences", REVIEW.SYSTEM)
        self.assertIn("normally no more than 70 words", REVIEW.SYSTEM)
        self.assertIn("then add\none context sentence", REVIEW.SYSTEM)
        self.assertIn("say what a policy changes", REVIEW.SYSTEM)
        self.assertIn("what conduct or process a complaint concerns", REVIEW.SYSTEM)
        self.assertIn("what\nconditions or deadline an approval imposes", REVIEW.SYSTEM)
        self.assertIn("one context sentence, do not publish", REVIEW.SYSTEM)
        self.assertEqual(15, REVIEW.MAX_OPTIONAL_READS)

    def test_past_alert_listing_is_bounded_and_searchable(self):
        target = {"past_alerts": [
            {"title": f"Routine item {index}", "topic": "policy"}
            for index in range(30)
        ] + [{"title": "New gym bond", "topic": "facilities"}]}

        bounded = REVIEW.list_past_alerts(target)
        matching = REVIEW.list_past_alerts(target, query="gym bond", limit=5)

        self.assertEqual(20, bounded["returned"])
        self.assertEqual(31, bounded["total"])
        self.assertEqual(["New gym bond"], [alert["title"] for alert in matching["alerts"]])

    def test_overview_is_chunked_instead_of_dumped(self):
        result = REVIEW.read_overview({"overview": {"body": "x" * 10_000}}, max_chars=1000)

        self.assertEqual(1000, len(result["text"]))
        self.assertEqual(1000, result["nextOffset"])

    def test_document_read_schema_caps_granular_reads(self):
        max_chars = REVIEW.READ_DOCUMENT_CONTENT_TOOL["input_schema"]["properties"]["maxChars"]
        self.assertEqual(12_000, max_chars["maximum"])
        self.assertEqual(4_000, REVIEW.DEFAULT_DOCUMENT_READ_CHARS)

    def test_matching_windows_find_text_beyond_initial_excerpt(self):
        content = "start " + ("x" * 90000) + " critical construction relocation details"

        windows = REVIEW._matching_windows(content, "construction relocation", 8000)

        self.assertTrue(windows)
        self.assertGreater(windows[0]["start"], 80000)
        self.assertIn("construction relocation", windows[0]["text"])

    def test_read_tool_dispatch_is_district_scoped(self):
        target = {"district_id": 42}
        call = {"name": "get_alert_detail", "input": {"alertKey": "safety-risk"}}
        with patch.object(REVIEW, "get_alert_detail", return_value={"alert": {"title": "Risk"}}) as detail:
            result = REVIEW.execute_read_tool(target, call)
        detail.assert_called_once_with(42, "safety-risk")
        self.assertEqual("Risk", result["alert"]["title"])

    def test_malformed_alerts_error_reports_shape_without_values(self):
        responses = [
            self.Response({"id": "response-1", "output": [{"type": "function_call", "call_id": "past-1",
                                        "name": "list_past_alerts", "arguments": "{}"}]}),
            self.Response({"id": "response-2", "output": [{"type": "function_call", "call_id": "publish-1",
                                        "name": "publish_alert_candidates", "arguments": json.dumps({
                                            "alerts": "private model output"
                                        })}]}),
        ]
        target = {"name": "Example", "past_alerts": [], "overview": None}
        documents = [{"id": 1, "meeting_date": "2026-08-20", "kind": "minutes",
                      "title": "Minutes", "text": "text"}]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), patch.object(
            REVIEW.requests, "post", side_effect=responses
        ), self.assertRaisesRegex(
            RuntimeError, r"malformed alerts list \(type=str; input_keys=\['alerts'\]\)"
        ) as raised:
            REVIEW.call_model(target, documents)

        self.assertNotIn("private model output", str(raised.exception))


class DocumentSearchMigrationTests(unittest.TestCase):
    def test_migration_indexes_complete_extracted_content(self):
        migration = (ROOT / "migrations" / "0001_baseline.sql").read_text()
        self.assertRegex(migration, r"content\s+text not null")
        self.assertRegex(migration, r"generated always as \(to_tsvector\('english',\s*content\)\) stored")
        self.assertRegex(migration, r"using gin\s*\(search_vector\)")


class EnvironmentGuardTests(unittest.TestCase):
    def test_main_refuses_production_without_explicit_feature_flag(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "OPENAI_API_KEY": "test"}), \
             patch.object(REVIEW, "load_env"), \
             patch("sys.argv", ["review_alerts.py", "--run-id", "1"]):
            os.environ.pop("ALERT_REVIEW_ENABLED", None)
            with self.assertRaisesRegex(RuntimeError, "ALERT_REVIEW_ENABLED=true"):
                REVIEW.main()

    def test_explicitly_enabled_production_review_persists_empty_result(self):
        target = {"slug": "example", "name": "Example"}
        stored = {"stored": [], "newAlertIds": []}
        with patch.dict(os.environ, {
            "APP_ENV": "production", "ALERT_REVIEW_ENABLED": "true",
            "OPENAI_API_KEY": "test"
        }), patch.object(REVIEW, "load_env"), \
             patch.object(REVIEW, "documents_for_run", return_value=(target, [])), \
             patch.object(REVIEW, "put_reviewed_alerts", return_value=stored) as put, \
             patch("sys.argv", ["review_alerts.py", "--run-id", "42"]):
            REVIEW.main()
        put.assert_called_once_with(42, "example", [], {})


if __name__ == "__main__":
    unittest.main()
