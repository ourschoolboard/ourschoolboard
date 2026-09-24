import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
TOOLS = ROOT / "scripts" / "tools"
sys.path[:0] = [str(PIPELINE), str(TOOLS)]
SPEC = importlib.util.spec_from_file_location("pipeline_execute_transactions", PIPELINE / "execute.py")
EXECUTE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXECUTE)


class RecordingCursor:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        return next(self.rows)


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class CursorConnection(Connection):
    def __init__(self, rows):
        super().__init__()
        self.cursor_instance = RecordingCursor(rows)

    def cursor(self):
        return self.cursor_instance


class ConnectionContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_args):
        return False


class DueCursor:
    def __init__(self, schedules):
        self.schedules = schedules
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.schedules


class ScheduleClaimTests(unittest.TestCase):
    def test_due_claims_advance_interval_and_board_schedules(self):
        claimed_at = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        board = {
            "timezone": "America/Los_Angeles",
            "run_time": "09:00",
            "meetings": {"ordinal": 2, "weekday": "wednesday"},
            "agenda_days_before": 5,
            "follow_up_days_after": 3,
        }
        cursor = DueCursor([
            {"slug": "interval-ca", "schedule_id": 10, "cadence_days": 14,
             "schedule_config": None, "claimed_at": claimed_at},
            {"slug": "calendar-ca", "schedule_id": 11, "cadence_days": 14,
             "schedule_config": board, "claimed_at": claimed_at},
        ])
        connection = Connection()
        with patch.object(
            EXECUTE, "connect", return_value=ConnectionContext(connection)
        ), patch.object(EXECUTE, "dict_cursor", return_value=cursor):
            slugs = EXECUTE.due_slugs(5)

        self.assertEqual(slugs, ["interval-ca", "calendar-ca"])
        self.assertIn("for update of q skip locked", cursor.calls[0][0])
        self.assertEqual(cursor.calls[0][1], (5,))
        self.assertEqual(cursor.calls[1][1], (claimed_at + timedelta(days=14), 10))
        self.assertEqual(
            cursor.calls[2][1],
            (datetime(2026, 9, 4, 16, tzinfo=timezone.utc), 11),
        )


class DocumentIdentityTests(unittest.TestCase):
    def test_unseen_url_is_stored_once_without_fingerprint(self):
        cursor = RecordingCursor([
            {"id": 41},
            {"id": 99},
        ])
        stored = {
            "key": "sources/aa/raw.pdf",
            "contentType": "application/pdf",
            "bytes": 123,
            "sha256": "a" * 64,
        }
        target = {
            "id": 3,
            "district_id": 7,
            "slug": "example-ca",
            "config": {},
            "document_identities": {},
            "seen_urls": set(),
        }
        candidate = {
            "document_url": "https://district.example/packet.pdf",
            "meeting_date": "2026-08-01",
            "title": "Board packet",
            "kind": "packet",
        }

        with patch.object(EXECUTE, "dict_cursor", return_value=cursor), \
                patch.object(EXECUTE, "download", return_value="application/pdf"), \
                patch.object(EXECUTE, "put_file", return_value=stored):
            created = EXECUTE.store_candidate(object(), target, 11, candidate)

        self.assertTrue(created)
        version_sql = next(
            sql for sql, _params in cursor.calls
            if "agency_document_versions" in sql and "insert into" in sql
        )
        meeting_sql = next(
            sql for sql, _params in cursor.calls
            if "agency_meetings" in sql and "insert into" in sql
        )
        self.assertIn("on conflict do nothing", version_sql)
        self.assertNotIn("fingerprint", version_sql)
        self.assertIn("on conflict (agency_id,source_url) where origin='scraped'", meeting_sql)
        self.assertIn("url:https://district.example/packet.pdf", target["document_identities"])
        self.assertIn("filename:packet.pdf", target["document_identities"])

    def test_same_filename_at_changed_link_is_not_downloaded(self):
        cursor = RecordingCursor([])
        target = {
            "id": 3, "district_id": 7, "slug": "example-ca", "config": {},
            "document_identities": {"filename:packet.pdf": 41},
        }
        candidate = {
            "document_url": "https://cdn.example/signed/packet.pdf?token=new",
            "meeting_date": "2026-08-01", "title": "Board packet", "kind": "packet",
        }

        with patch.object(EXECUTE, "dict_cursor", return_value=cursor), \
                patch.object(EXECUTE, "download") as download:
            created = EXECUTE.store_candidate(object(), target, 11, candidate)

        self.assertFalse(created)
        download.assert_not_called()
        self.assertEqual(2, len(cursor.calls))

    def test_content_disposition_filename_matches_after_download(self):
        cursor = RecordingCursor([])
        target = {
            "id": 3, "district_id": 7, "slug": "example-ca", "config": {},
            "document_identities": {"filename:packet.pdf": 41},
        }
        candidate = {
            "document_url": "https://cdn.example/download?id=new-token",
            "meeting_date": "2026-08-01", "title": "Board packet", "kind": "packet",
        }

        def downloaded(_url, _path, **kwargs):
            kwargs["metadata"]["filename"] = "Packet.pdf"
            return "application/pdf"

        with patch.object(EXECUTE, "dict_cursor", return_value=cursor), \
                patch.object(EXECUTE, "download", side_effect=downloaded), \
                patch.object(EXECUTE, "put_file") as put_file:
            created = EXECUTE.store_candidate(object(), target, 11, candidate)

        self.assertFalse(created)
        put_file.assert_not_called()
        self.assertEqual(2, len(cursor.calls))

    def test_fragment_does_not_make_same_link_new(self):
        original = EXECUTE.document_identities("https://district.example/doc?id=12#page=2")
        repeated = EXECUTE.document_identities("https://district.example/doc?id=12")
        self.assertEqual(original, repeated)

    def test_generic_endpoint_name_is_not_used_as_filename(self):
        identities = EXECUTE.document_identities("https://drive.google.com/file/abc/view")
        self.assertEqual({"url:https://drive.google.com/file/abc/view"}, identities)

    def test_link_and_content_filenames_are_both_identities(self):
        identities = EXECUTE.document_identities(
            "https://district.example/export.pdf", "Board Packet.pdf"
        )
        self.assertIn("filename:export.pdf", identities)
        self.assertIn("filename:board packet.pdf", identities)

    def test_generic_viewer_script_is_not_used_as_filename(self):
        # Granicus's classic MediaManager (DocumentViewer.php, AgendaViewer.php,
        # MinutesViewer.php, MediaPlayer.php) serves every distinct document
        # through the same script path, with real identity only in the query
        # string. Confirmed to silently collapse distinct documents into one
        # false duplicate before this fix.
        first = EXECUTE.document_identities(
            "https://cityofstamford.granicus.com/DocumentViewer.php?file=agenda1.pdf&view=1"
        )
        second = EXECUTE.document_identities(
            "https://cityofstamford.granicus.com/DocumentViewer.php?file=agenda2.pdf&view=1"
        )
        self.assertTrue(first.isdisjoint(second), f"{first} collided with {second}")
        self.assertNotIn("filename:documentviewer.php", first)

    def test_generic_diligent_file_handler_is_not_used_as_filename(self):
        # Diligent Community's generic /File.html?handle=... viewer serves
        # every document through the same page path. Confirmed to silently
        # collapse distinct documents into one false duplicate before this
        # fix.
        first = EXECUTE.document_identities(
            "https://dpsnc.community.diligentoneplatform.com/document/11600/"
            "File.html?handle=AAAA1111"
        )
        second = EXECUTE.document_identities(
            "https://dpsnc.community.diligentoneplatform.com/document/11601/"
            "File.html?handle=BBBB2222"
        )
        self.assertTrue(first.isdisjoint(second), f"{first} collided with {second}")
        self.assertNotIn("filename:file.html", first)

    def test_generic_handler_response_filename_is_not_used_as_identity(self):
        # Legistar returns Agenda.pdf for every distinct View.ashx agenda.
        # The response filename must not collapse separate meeting URLs.
        first = EXECUTE.document_identities(
            "https://ousd.legistar.com/View.ashx?M=A&ID=1428569", "Agenda.pdf"
        )
        second = EXECUTE.document_identities(
            "https://ousd.legistar.com/View.ashx?M=A&ID=1437110", "Agenda.pdf"
        )
        self.assertTrue(first.isdisjoint(second), f"{first} collided with {second}")
        self.assertNotIn("filename:agenda.pdf", first)

    def test_real_document_extension_through_a_query_string_still_collides_as_intended(self):
        # The existing "same filename at a rotated signed link" behavior
        # (see test_same_filename_at_changed_link_is_not_downloaded above)
        # must still work -- only dynamic-script suffixes are excluded, not
        # every filename that happens to carry a query string.
        first = EXECUTE.document_identities(
            "https://cdn.example/signed/packet.pdf?token=aaa"
        )
        second = EXECUTE.document_identities(
            "https://cdn.example/signed/packet.pdf?token=bbb"
        )
        self.assertIn("filename:packet.pdf", first)
        self.assertFalse(first.isdisjoint(second))

    def test_replaces_non_pdf_canonical_drive_record_when_resolver_enabled(self):
        cursor = RecordingCursor([{"id": 41}, {"id": 99}])
        url = "https://drive.google.com/file/d/Abc_123-X/view"
        target = {
            "id": 3, "district_id": 7, "slug": "example-ca",
            "config": {"google_drive_view_pdf": True},
            "document_identities": {f"url:{url}": 41},
            "refreshable_drive_source_urls": {url},
            "seen_urls": {url},
        }
        candidate = {
            "document_url": url, "meeting_date": "2026-08-01",
            "title": "Board packet", "kind": "packet",
        }
        stored = {
            "key": "sources/aa/refreshed.pdf", "contentType": "application/pdf",
            "bytes": 456, "sha256": "b" * 64,
        }
        with patch.object(EXECUTE, "dict_cursor", return_value=cursor), \
                patch.object(EXECUTE, "download", return_value="application/pdf") as download, \
                patch.object(EXECUTE, "put_file", return_value=stored):
            created = EXECUTE.store_candidate(object(), target, 11, candidate)
        self.assertTrue(created)
        download.assert_called_once()
        meeting_sql = next(
            sql for sql, _params in cursor.calls
            if "agency_meetings" in sql and "insert into" in sql
        )
        self.assertIn("storage_key=excluded.storage_key", meeting_sql)
        self.assertIn("content_type=excluded.content_type", meeting_sql)

    def test_does_not_refresh_drive_record_that_is_already_pdf(self):
        url = "https://drive.google.com/file/d/Abc_123-X/view"
        target = {
            "config": {"google_drive_view_pdf": True},
            "refreshable_drive_source_urls": set(),
        }
        self.assertFalse(EXECUTE.needs_google_drive_pdf_refresh(target, url))


class FailedRunTransactionTests(unittest.TestCase):
    def test_first_ever_scheduled_failure_retries_after_24_hours(self):
        connection = CursorConnection([("schedule", None)])

        EXECUTE.finish_run(
            connection,
            {"id": 2, "district_id": 4},
            18,
            status="failed",
            failure_kind="parse_error",
            error="selector missing",
        )

        _schedule_sql, schedule_params = connection.cursor_instance.calls[-1]
        self.assertEqual(schedule_params, (True, 4))

    def test_first_scheduled_failure_retries_once_after_24_hours(self):
        connection = CursorConnection([("schedule", "ok")])

        EXECUTE.finish_run(
            connection,
            {"id": 2, "district_id": 4},
            19,
            status="failed",
            failure_kind="fetch_error",
            error="HTTP 404",
        )

        schedule_sql, schedule_params = connection.cursor_instance.calls[-1]
        self.assertIn("interval '24 hours'", schedule_sql)
        self.assertEqual(schedule_params, (True, 4))

    def test_failed_retry_keeps_the_normal_weekly_schedule(self):
        connection = CursorConnection([("schedule", "failed")])

        EXECUTE.finish_run(
            connection,
            {"id": 2, "district_id": 4},
            20,
            status="failed",
            failure_kind="fetch_error",
            error="HTTP 404",
        )

        _schedule_sql, schedule_params = connection.cursor_instance.calls[-1]
        self.assertEqual(schedule_params, (False, 4))

    def test_manual_failure_does_not_change_the_schedule(self):
        connection = CursorConnection([("manual", "ok")])

        EXECUTE.finish_run(
            connection,
            {"id": 2, "district_id": 4},
            21,
            status="failed",
            failure_kind="fetch_error",
            error="HTTP 404",
        )

        _schedule_sql, schedule_params = connection.cursor_instance.calls[-1]
        self.assertEqual(schedule_params, (False, 4))

    def test_unavailable_youtube_transcript_does_not_abort_other_candidates(self):
        connection = Connection()
        candidates = [
            {"kind": "video", "document_url":
             "https://www.youtube.com/watch?v=Abc_123-Xyz"},
            {"kind": "minutes", "document_url": "https://district.example/minutes.pdf"},
        ]
        recorded = []
        with patch.object(
            EXECUTE, "connect", return_value=ConnectionContext(connection)
        ), patch.object(
            EXECUTE, "load_target", return_value={
                "id": 2, "district_id": 4, "source": "def scrape(ctx): pass",
                "seen_urls": set(), "config": {},
            }
        ), patch.object(
            EXECUTE, "start_run", return_value=(19, 0)
        ), patch.object(
            EXECUTE, "run_scraper", return_value={
                "ok": True, "meetings": candidates, "warnings": [], "rejected": 0,
            }
        ), patch.object(
            EXECUTE, "limit_video_transcriptions", return_value=(candidates, 0)
        ), patch.object(
            EXECUTE, "store_candidate", side_effect=[
                EXECUTE.YouTubeTranscriptError("captions unavailable"), True,
            ]
        ), patch.object(
            EXECUTE, "finish_run", side_effect=lambda *args, **kwargs: recorded.append(kwargs)
        ):
            result = EXECUTE.run_one("example-ca")

        self.assertTrue(result["ok"])
        self.assertEqual(result["documentsNew"], 1)
        self.assertEqual(recorded[0]["detail"]["videoTranscriptionsUnavailable"], 1)
        self.assertEqual(recorded[0]["detail"]["rejected"], 1)
        self.assertIn("Abc_123-Xyz", recorded[0]["detail"]["warnings"][0])

    def test_each_exception_handler_rolls_back_before_recording_failure(self):
        cases = (
            (EXECUTE.TerminationRequested("stopped"), "interrupted"),
            (EXECUTE.requests.RequestException("network"), "fetch_error"),
            (RuntimeError("unique violation"), "storage_error"),
        )
        for error, expected_kind in cases:
            with self.subTest(expected_kind=expected_kind):
                connection = Connection()
                recorded = []

                def finish(conn, target, run_id, **kwargs):
                    self.assertIs(conn, connection)
                    self.assertEqual(connection.rollbacks, 1)
                    recorded.append((target, run_id, kwargs))

                with patch.object(
                    EXECUTE, "connect", return_value=ConnectionContext(connection)
                ), patch.object(
                    EXECUTE, "load_target", return_value={
                        "id": 2,
                        "district_id": 4,
                        "source": "def scrape(ctx): pass",
                        "seen_urls": set(),
                        "config": {},
                    }
                ), patch.object(
                    EXECUTE, "start_run", return_value=(19, 0)
                ), patch.object(
                    EXECUTE, "run_scraper", side_effect=error
                ), patch.object(EXECUTE, "finish_run", side_effect=finish):
                    result = EXECUTE.run_one("example-ca")

                self.assertFalse(result["ok"])
                self.assertEqual(connection.commits, 1)
                self.assertEqual(connection.rollbacks, 1)
                self.assertEqual(recorded[0][2]["failure_kind"], expected_kind)


if __name__ == "__main__":
    unittest.main()
