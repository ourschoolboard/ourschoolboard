"""Normalize the schools affected by a published alert or past report."""
from __future__ import annotations


ALL_SCHOOLS = "all schools"
LEVEL_SCOPES = {"all_elementary", "all_middle", "all_high"}
MAX_SCHOOLS = 100
MAX_SCHOOL_NAME = 200


def normalize_school_scope(value, *, published: bool) -> list[str]:
    if value is None:
        if published:
            raise ValueError("schoolScope is required for publication")
        return []
    if not isinstance(value, list):
        raise ValueError("schoolScope must be an array")
    if len(value) > MAX_SCHOOLS:
        raise ValueError(f"schoolScope cannot exceed {MAX_SCHOOLS} schools")

    output: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"schoolScope[{index}] must be a non-empty string")
        school = raw.strip()
        if len(school) > MAX_SCHOOL_NAME:
            raise ValueError(
                f"schoolScope[{index}] exceeds {MAX_SCHOOL_NAME} characters"
            )
        key = school.casefold()
        if key == ALL_SCHOOLS or key in LEVEL_SCOPES:
            school = key
        if key not in seen:
            seen.add(key)
            output.append(school)

    if ALL_SCHOOLS in seen and len(output) != 1:
        raise ValueError('schoolScope must not mix "all schools" with school names')
    if published and not output:
        raise ValueError("schoolScope is required for publication")
    return output
