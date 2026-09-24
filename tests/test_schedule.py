import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "scripts" / "pipeline"
sys.path.insert(0, str(PIPELINE))
SPEC = importlib.util.spec_from_file_location("board_schedule", PIPELINE / "schedule.py")
SCHEDULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCHEDULE)


class BoardScheduleTest(unittest.TestCase):
    def recurring(self, **overrides):
        value = {
            "timezone": "America/Los_Angeles",
            "run_time": "09:00",
            "meetings": {"ordinal": 2, "weekday": "wednesday"},
            "agenda_days_before": 5,
            "follow_up_days_after": 3,
        }
        value.update(overrides)
        return value

    def test_nth_weekday_defaults_to_all_months(self):
        normalized = SCHEDULE.validate_schedule(self.recurring())
        self.assertEqual(normalized["meetings"]["mode"], "nth_weekday")
        self.assertEqual(normalized["meetings"]["months"], list(range(1, 13)))

    def test_next_run_is_agenda_check_before_second_wednesday(self):
        after = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        result = SCHEDULE.next_run_after(self.recurring(), after)
        self.assertEqual(result, datetime(2026, 9, 4, 16, tzinfo=timezone.utc))

    def test_next_run_advances_to_post_meeting_check(self):
        after = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)
        result = SCHEDULE.next_run_after(self.recurring(), after)
        self.assertEqual(result, datetime(2026, 9, 12, 16, tzinfo=timezone.utc))

    def test_last_weekday_and_dst_are_supported(self):
        schedule = self.recurring(meetings={
            "ordinal": "last", "weekday": "monday", "months": [11]
        })
        result = SCHEDULE.next_run_after(
            schedule, datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(result, datetime(2026, 11, 25, 17, tzinfo=timezone.utc))

    def test_custom_dates_replace_recurring_pattern(self):
        schedule = self.recurring(meetings={
            "mode": "custom_dates",
            "dates": ["2026-10-01", "2026-12-17"],
        })
        result = SCHEDULE.next_run_after(
            schedule, datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        self.assertEqual(result, datetime(2026, 9, 26, 16, tzinfo=timezone.utc))

    def test_exhausted_custom_dates_return_none(self):
        schedule = self.recurring(meetings={
            "mode": "custom_dates", "dates": ["2026-01-10"]
        })
        result = SCHEDULE.next_run_after(
            schedule, datetime(2026, 9, 20, tzinfo=timezone.utc)
        )
        self.assertIsNone(result)

    def test_invalid_values_are_rejected(self):
        invalid = [
            self.recurring(agenda_days_before=31),
            self.recurring(follow_up_days_after=True),
            self.recurring(timezone="Mars/Olympus"),
            self.recurring(meetings={"mode": "custom_dates", "dates": ["soon"]}),
            self.recurring(meetings={"ordinal": 0, "weekday": "wednesday"}),
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                SCHEDULE.validate_schedule(value)


if __name__ == "__main__":
    unittest.main()
