#!/usr/bin/env python3
"""Create, read, and update district local-vote records through a narrow DB tool.

A local vote is a board election, an override/bond/parcel-tax question, or a
town-meeting article whose outcome directly concerns the district. Rows are
upserted by a stable district-scoped vote key so refreshes update rather than
duplicate. This module intentionally exposes no delete or arbitrary SQL
operation.

Unlike alerts, a vote's source may live outside the collected meeting corpus:
town clerks and the Secretary of the Commonwealth publish election calendars
and certified results the district itself never posts. Sources must still be
official HTTPS pages, and outcome statuses require one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, load_env, t  # noqa: E402

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VOTE_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VOTE_KINDS = {
    "board_election", "appointment", "override", "debt_exclusion", "bond",
    "parcel_tax", "charter_question", "other",
}
VOTE_VENUES = {"ballot", "town_meeting", "city_council", "appointing_body"}
VOTE_STATUSES = {
    "draft", "proposed", "qualified", "scheduled", "passed", "failed",
    "withdrawn",
}
OUTCOME_STATUSES = {"passed", "failed"}
MAX_FILE_BYTES = 256 * 1024
MAX_LIST = 1_000
MAX_DETAIL_BYTES = 16 * 1024


def require_staging_env() -> None:
    load_env()
    if os.environ.get("APP_ENV") != "staging":
        raise ValueError("refusing to write outside staging (APP_ENV=staging)")


def read_json(path: Path):
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("vote file exceeds 256KB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, (dict, list)):
        raise ValueError("vote file must contain a JSON object or array")
    return value


def require_slug(value: str) -> str:
    if not SLUG.fullmatch(value or ""):
        raise ValueError("district slug must be lowercase words separated by hyphens")
    return value


def require_text(value, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    clean = value.strip()
    if len(clean) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return clean


def optional_text(value, field: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return require_text(value, field, maximum)


def optional_date(value, field: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)") from exc


def optional_https_url(value, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"{field} must be an https URL")
    return value.strip()


def optional_amount(value, field: str):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if number < 0:
        raise ValueError(f"{field} must not be negative")
    return number


def normalize_vote(payload: dict) -> dict:
    vote_key = payload.get("voteKey") or payload.get("vote_key") or payload.get("id")
    if not isinstance(vote_key, str) or not VOTE_KEY.fullmatch(vote_key):
        raise ValueError("voteKey must be lowercase words separated by hyphens")
    kind = require_text(payload.get("kind"), "kind", 40)
    if kind not in VOTE_KINDS:
        raise ValueError(f"kind must be one of {sorted(VOTE_KINDS)}")
    venue = require_text(payload.get("venue"), "venue", 20)
    if venue not in VOTE_VENUES:
        raise ValueError(f"venue must be one of {sorted(VOTE_VENUES)}")
    status = require_text(payload.get("status"), "status", 20)
    if status not in VOTE_STATUSES:
        raise ValueError(f"status must be one of {sorted(VOTE_STATUSES)}")
    detail = payload.get("detail")
    if detail is None:
        detail = {}
    if not isinstance(detail, dict):
        raise ValueError("detail must be an object")
    if len(json.dumps(detail)) > MAX_DETAIL_BYTES:
        raise ValueError("detail exceeds 16KB")
    vote = {
        "vote_key": vote_key,
        "kind": kind,
        "venue": venue,
        "title": require_text(payload.get("title"), "title", 300),
        "summary": optional_text(payload.get("summary"), "summary", 2000),
        "amount": optional_amount(payload.get("amount"), "amount"),
        "jurisdiction": optional_text(payload.get("jurisdiction"), "jurisdiction", 120),
        "vote_date": optional_date(
            payload.get("voteDate") or payload.get("vote_date") or payload.get("date"),
            "voteDate",
        ),
        "status": status,
        "detail": detail,
        "source_url": optional_https_url(
            payload.get("sourceUrl") or payload.get("source_url"), "sourceUrl"
        ),
        "last_verified": optional_date(
            payload.get("lastVerified") or payload.get("last_verified"),
            "lastVerified",
        ),
    }
    if status in OUTCOME_STATUSES and not vote["source_url"]:
        raise ValueError(
            "passed/failed status requires a sourceUrl citing an official record"
        )
    if status in {"qualified", "scheduled"} and not vote["vote_date"]:
        raise ValueError(f"{status} status requires voteDate")
    return vote


def district_id(conn, slug: str) -> int:
    with dict_cursor(conn) as cur:
        cur.execute(f"select id from {t('districts')} where slug=%s", (slug,))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown district: {slug}")
    return row["id"]


def put_vote(slug: str, payload) -> list[dict]:
    require_staging_env()
    items = payload if isinstance(payload, list) else payload.get("votes", [payload])
    if not isinstance(items, list) or not items:
        raise ValueError("vote file must contain a vote or non-empty votes array")
    clean = [normalize_vote(item) for item in items]
    output = []
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            for vote in clean:
                cur.execute(
                    f"""insert into {t('local_votes')}
                          (district_id, vote_key, kind, venue, title, summary,
                           amount, jurisdiction, vote_date, status, detail,
                           source_url, last_verified, updated_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,now())
                        on conflict (district_id, vote_key) do update set
                          kind=excluded.kind, venue=excluded.venue,
                          title=excluded.title, summary=excluded.summary,
                          amount=excluded.amount,
                          jurisdiction=excluded.jurisdiction,
                          vote_date=excluded.vote_date, status=excluded.status,
                          detail=excluded.detail,
                          source_url=excluded.source_url,
                          last_verified=excluded.last_verified,
                          updated_at=now()
                        returning id, vote_key, status, updated_at""",
                    (did, vote["vote_key"], vote["kind"], vote["venue"],
                     vote["title"], vote["summary"], vote["amount"],
                     vote["jurisdiction"], vote["vote_date"], vote["status"],
                     json.dumps(vote["detail"]), vote["source_url"],
                     vote["last_verified"]),
                )
                row = cur.fetchone()
                output.append({k: str(v) for k, v in row.items()})
    return output


def list_votes(slug: str) -> dict:
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select vote_key, kind, venue, title, summary, amount,
                           jurisdiction, vote_date, status, detail, source_url,
                           last_verified, updated_at
                      from {t('local_votes')}
                     where district_id=%s
                     order by vote_date desc nulls last, id desc
                     limit %s""",
                (did, MAX_LIST),
            )
            rows = [dict(r) for r in cur.fetchall()]
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (date,)):
                row[key] = value.isoformat()
            elif key in {"amount"} and value is not None:
                row[key] = float(value)
            elif key == "updated_at":
                row[key] = str(value)
    return {"count": len(rows), "votes": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_put = sub.add_parser("put", help="insert or update votes by voteKey")
    p_put.add_argument("--district", required=True)
    p_put.add_argument("file", type=Path)
    p_list = sub.add_parser("list", help="list a district's votes")
    p_list.add_argument("--district", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "put":
            result = put_vote(require_slug(args.district), read_json(args.file))
        else:
            result = list_votes(require_slug(args.district))
        print(json.dumps({"ok": True, "result": result}, default=str))
        return 0
    except Exception as exc:  # surfaced as structured output, not a traceback
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
