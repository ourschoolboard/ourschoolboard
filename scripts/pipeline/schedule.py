"""Validation and next-run calculation for district scrape schedules."""
from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
MAX_EXPLICIT_DATES = 100


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"schedule.{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"schedule.{name} must be between {minimum} and {maximum}")
    return value


def validate_schedule(value: dict) -> dict:
    """Return a normalized board-calendar schedule or raise ValueError."""
    if not isinstance(value, dict):
        raise ValueError("schedule must be a JSON object")
    allowed = {
        "type", "timezone", "run_time", "meetings",
        "agenda_days_before", "follow_up_days_after",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown schedule option: {unknown[0]}")
    if value.get("type", "board_calendar") != "board_calendar":
        raise ValueError("schedule.type must be board_calendar")

    timezone_name = value.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise ValueError("schedule.timezone is required")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("schedule.timezone must be an IANA timezone") from exc

    run_time = value.get("run_time", "09:00")
    try:
        parsed_time = time.fromisoformat(run_time)
    except (TypeError, ValueError) as exc:
        raise ValueError("schedule.run_time must be HH:MM") from exc
    if parsed_time.second or parsed_time.microsecond or len(run_time) != 5:
        raise ValueError("schedule.run_time must be HH:MM")

    agenda_days = _integer(
        value.get("agenda_days_before"), "agenda_days_before", 0, 30
    )
    follow_up_days = _integer(
        value.get("follow_up_days_after"), "follow_up_days_after", 0, 60
    )
    meetings = value.get("meetings")
    if not isinstance(meetings, dict):
        raise ValueError("schedule.meetings is required")
    mode = meetings.get("mode", "nth_weekday")
    if mode == "nth_weekday":
        allowed_meetings = {"mode", "ordinal", "weekday", "months"}
        unknown = sorted(set(meetings) - allowed_meetings)
        if unknown:
            raise ValueError(f"unknown schedule.meetings option: {unknown[0]}")
        ordinal = meetings.get("ordinal")
        if ordinal != "last":
            ordinal = _integer(ordinal, "meetings.ordinal", 1, 5)
        weekday = meetings.get("weekday")
        if weekday not in WEEKDAYS:
            raise ValueError(
                "schedule.meetings.weekday must be a lowercase weekday name"
            )
        months = meetings.get("months", list(range(1, 13)))
        if (not isinstance(months, list) or not months
                or len(set(months)) != len(months)):
            raise ValueError("schedule.meetings.months must be a non-empty unique list")
        for month in months:
            _integer(month, "meetings.months[]", 1, 12)
        normalized_meetings = {
            "mode": mode,
            "ordinal": ordinal,
            "weekday": weekday,
            "months": sorted(months),
        }
    elif mode == "custom_dates":
        if set(meetings) - {"mode", "dates"}:
            unknown = sorted(set(meetings) - {"mode", "dates"})[0]
            raise ValueError(f"unknown schedule.meetings option: {unknown}")
        dates = meetings.get("dates")
        if (not isinstance(dates, list) or not dates
                or len(dates) > MAX_EXPLICIT_DATES or len(set(dates)) != len(dates)):
            raise ValueError(
                f"schedule.meetings.dates must contain 1-{MAX_EXPLICIT_DATES} unique dates"
            )
        parsed_dates = []
        for item in dates:
            try:
                parsed_dates.append(date.fromisoformat(item).isoformat())
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "schedule.meetings.dates must contain YYYY-MM-DD dates"
                ) from exc
        normalized_meetings = {"mode": mode, "dates": sorted(parsed_dates)}
    else:
        raise ValueError(
            "schedule.meetings.mode must be nth_weekday or custom_dates"
        )

    return {
        "type": "board_calendar",
        "timezone": timezone_name,
        "run_time": run_time,
        "meetings": normalized_meetings,
        "agenda_days_before": agenda_days,
        "follow_up_days_after": follow_up_days,
    }


def _nth_weekday(year: int, month: int, weekday: int, ordinal) -> date | None:
    _, days_in_month = calendar.monthrange(year, month)
    if ordinal == "last":
        candidate = date(year, month, days_in_month)
        return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)
    first = date(year, month, 1)
    day = 1 + (weekday - first.weekday()) % 7 + 7 * (ordinal - 1)
    return date(year, month, day) if day <= days_in_month else None


def _meeting_dates(schedule: dict, local_now: datetime) -> list[date]:
    meetings = schedule["meetings"]
    if meetings["mode"] == "custom_dates":
        return [date.fromisoformat(item) for item in meetings["dates"]]
    dates = []
    for year in range(local_now.year - 1, local_now.year + 4):
        for month in meetings["months"]:
            meeting = _nth_weekday(
                year, month, WEEKDAYS[meetings["weekday"]], meetings["ordinal"]
            )
            if meeting is not None:
                dates.append(meeting)
    return dates


def next_run_after(schedule: dict, after: datetime | None = None) -> datetime | None:
    """Return the next UTC run after an instant, or None for an exhausted calendar."""
    normalized = validate_schedule(schedule)
    after = after or datetime.now(timezone.utc)
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    zone = ZoneInfo(normalized["timezone"])
    local_now = after.astimezone(zone)
    hour, minute = map(int, normalized["run_time"].split(":"))
    candidates = set()
    for meeting in _meeting_dates(normalized, local_now):
        for delta_days in (
            -normalized["agenda_days_before"],
            normalized["follow_up_days_after"],
        ):
            local_run = datetime.combine(
                meeting + timedelta(days=delta_days), time(hour, minute), zone
            )
            if local_run > local_now:
                candidates.add(local_run.astimezone(timezone.utc))
    return min(candidates) if candidates else None
