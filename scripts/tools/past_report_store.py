#!/usr/bin/env python3
"""Create, read, and update district past reports through a narrow DB tool.

The onboarding agent selects a district by slug and can only access rows owned
by that district. This module intentionally exposes no delete or arbitrary SQL
operation, and verifies every object-store key against the district's collected
source records before storing it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, load_env, t  # noqa: E402
from school_scope import normalize_school_scope  # noqa: E402


SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REPORT_KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REPORT_TYPES = {
    "board_meeting",
    "local_leadership_meeting",
    "local_school_announcement",
    "budget_publication",
    "policy_publication",
    "plan_publication",
    "audit_publication",
    "other_official_report",
}
REPORT_STATUSES = {"draft", "published"}
LINK_FIELDS = {"url", "title", "sourceType", "storageKey", "contentType"}
MAX_REPORT_FILE_BYTES = 1024 * 1024
MAX_LINKS = 50
MAX_LIST = 1_000


def require_staging_env() -> None:
    load_env()
    if os.environ.get("APP_ENV") != "staging":
        raise ValueError("refusing to write outside staging (APP_ENV=staging)")


def read_json(path: Path):
    if path.stat().st_size > MAX_REPORT_FILE_BYTES:
        raise ValueError("report file exceeds 1MB")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("report file must contain a JSON object")
    return value


def require_slug(value: str) -> str:
    if not SLUG.fullmatch(value or ""):
        raise ValueError("district slug must be lowercase words separated by hyphens")
    return value


def require_text(value, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required and must be a string")
    clean = value.strip()
    if len(clean) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return clean


def optional_text(value, field: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    return require_text(value, field, maximum)


def normalize_document_links(value) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("documentLinks is required and must be a non-empty array")
    if len(value) > MAX_LINKS:
        raise ValueError(f"documentLinks cannot exceed {MAX_LINKS} items")

    output = []
    for index, raw in enumerate(value):
        field = f"documentLinks[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{field} must be an object")
        unknown = set(raw) - LINK_FIELDS
        if unknown:
            raise ValueError(f"{field} has unsupported fields: {sorted(unknown)}")

        has_url = raw.get("url") not in (None, "")
        has_storage = raw.get("storageKey") not in (None, "")
        if has_url == has_storage:
            raise ValueError(f"{field} must have exactly one of url or storageKey")

        title = require_text(raw.get("title"), f"{field}.title", 500)
        if has_url:
            if raw.get("contentType") not in (None, ""):
                raise ValueError(f"{field}.contentType is only valid with storageKey")
            url = require_text(raw.get("url"), f"{field}.url", 2048)
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"{field}.url must be an HTTP(S) URL")
            if parsed.username or parsed.password:
                raise ValueError(f"{field}.url must not contain URL credentials")
            link = {
                "url": url,
                "title": title,
                "sourceType": optional_text(
                    raw.get("sourceType"), f"{field}.sourceType", 200
                ),
            }
        else:
            storage_key = require_text(
                raw.get("storageKey"), f"{field}.storageKey", 1024
            )
            if not storage_key.startswith("sources/") or ".." in storage_key.split("/"):
                raise ValueError(f"{field}.storageKey must be a source object key")
            link = {
                "storageKey": storage_key,
                "title": title,
                "sourceType": optional_text(
                    raw.get("sourceType"), f"{field}.sourceType", 200
                ),
                "contentType": optional_text(
                    raw.get("contentType"), f"{field}.contentType", 255
                ),
            }
        output.append({key: item for key, item in link.items() if item is not None})
    return output


def normalize_report(payload: dict) -> dict:
    report_key = require_text(
        payload.get("reportKey") or payload.get("report_key"), "reportKey", 300
    )
    if not REPORT_KEY.fullmatch(report_key):
        raise ValueError("reportKey must be lowercase words separated by hyphens")

    raw_date = payload.get("reportDate") if "reportDate" in payload else payload.get("report_date")
    if raw_date is None:
        report_date = None
    else:
        report_date = require_text(raw_date, "reportDate", 10)
        date.fromisoformat(report_date)

    report_type = require_text(
        payload.get("reportType") or payload.get("report_type"), "reportType", 100
    )
    if report_type not in REPORT_TYPES:
        raise ValueError(f"reportType must be one of {sorted(REPORT_TYPES)}")

    status = str(payload.get("status") or "draft").strip()
    if status not in REPORT_STATUSES:
        raise ValueError(f"status must be one of {sorted(REPORT_STATUSES)}")

    return {
        "report_key": report_key,
        "report_date": report_date,
        "report_type": report_type,
        "title": require_text(payload.get("title"), "title", 500),
        "summary": require_text(payload.get("summary"), "summary", 10_000),
        "school_scope": normalize_school_scope(
            payload.get("schoolScope", payload.get("school_scope")),
            published=status == "published",
        ),
        "document_links": normalize_document_links(
            payload.get("documentLinks", payload.get("document_links"))
        ),
        "status": status,
    }


def district_id(conn, slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"select id from {t('districts')} where slug=%s", (require_slug(slug),)
        )
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown district: {slug}")
    return row[0]


def verify_document_links(conn, did: int, links: list[dict]) -> None:
    urls = sorted({item["url"] for item in links if "url" in item})
    keys = sorted({item["storageKey"] for item in links if "storageKey" in item})
    with conn.cursor() as cur:
        if urls:
            cur.execute(
                f"""select distinct m.source_url
                    from {t('agency_meetings')} m
                    join {t('district_documents')} x on x.meeting_id=m.id
                    where x.district_id=%s and m.source_url=any(%s)""",
                (did, urls),
            )
            found_urls = {row[0] for row in cur.fetchall()}
            missing_urls = sorted(set(urls) - found_urls)
        else:
            missing_urls = []
        if keys:
            cur.execute(
                f"""select distinct source.storage_key
                    from (
                      select m.storage_key
                      from {t('agency_meetings')} m
                      join {t('district_documents')} x on x.meeting_id=m.id
                      where x.district_id=%s and m.storage_key=any(%s)
                      union
                      select v.storage_key
                      from {t('agency_document_versions')} v
                      join {t('agency_meetings')} m on m.id=v.meeting_id
                      join {t('district_documents')} x on x.meeting_id=m.id
                      where x.district_id=%s and v.storage_key=any(%s)
                    ) source""",
                (did, keys, did, keys),
            )
            found_keys = {row[0] for row in cur.fetchall()}
            missing_keys = sorted(set(keys) - found_keys)
        else:
            missing_keys = []
    if missing_urls:
        raise ValueError(
            "url is not collected for this district: " + ", ".join(missing_urls)
        )
    if missing_keys:
        raise ValueError(
            "storageKey is not collected for this district: " + ", ".join(missing_keys)
        )


def create_report(slug: str, payload: dict) -> dict:
    require_staging_env()
    item = normalize_report(payload)
    with connect() as conn:
        did = district_id(conn, slug)
        verify_document_links(conn, did, item["document_links"])
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""insert into {t('past_reports')}
                      (district_id,report_key,report_date,report_type,title,summary,
                       school_scope,document_links,status,published_at,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                      case when %s='published' then now() end,now())
                    returning id,report_key,report_date,report_type,title,summary,
                              school_scope,document_links,status,published_at,created_at,updated_at""",
                (
                    did,
                    item["report_key"],
                    item["report_date"],
                    item["report_type"],
                    item["title"],
                    item["summary"],
                    json.dumps(item["school_scope"]),
                    json.dumps(item["document_links"]),
                    item["status"],
                    item["status"],
                ),
            )
            return dict(cur.fetchone())


def selector(report_id: int | None, report_key: str | None) -> tuple[int | None, str | None]:
    if (report_id is None) == (report_key is None):
        raise ValueError("select a report by exactly one of id or reportKey")
    if report_id is not None and report_id < 1:
        raise ValueError("report id must be positive")
    if report_key is not None:
        report_key = require_text(report_key, "reportKey", 300)
        if not REPORT_KEY.fullmatch(report_key):
            raise ValueError("reportKey must be lowercase words separated by hyphens")
    return report_id, report_key


def update_report(
    slug: str, payload: dict, report_id: int | None = None, report_key: str | None = None
) -> dict:
    require_staging_env()
    item = normalize_report(payload)
    report_id, report_key = selector(report_id, report_key)
    selector_sql = "id=%s" if report_id is not None else "report_key=%s"
    selector_value = report_id if report_id is not None else report_key
    with connect() as conn:
        did = district_id(conn, slug)
        verify_document_links(conn, did, item["document_links"])
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""update {t('past_reports')} set
                      report_key=%s,report_date=%s,report_type=%s,title=%s,summary=%s,
                      school_scope=%s::jsonb,document_links=%s::jsonb,status=%s,
                      published_at=case
                        when %s='published' then coalesce(published_at,now())
                        else null end,
                      updated_at=now()
                    where district_id=%s and {selector_sql}
                    returning id,report_key,report_date,report_type,title,summary,
                              school_scope,document_links,status,published_at,created_at,updated_at""",
                (
                    item["report_key"],
                    item["report_date"],
                    item["report_type"],
                    item["title"],
                    item["summary"],
                    json.dumps(item["school_scope"]),
                    json.dumps(item["document_links"]),
                    item["status"],
                    item["status"],
                    did,
                    selector_value,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("unknown report for district")
            return dict(row)


def get_report(
    slug: str, report_id: int | None = None, report_key: str | None = None
) -> dict:
    report_id, report_key = selector(report_id, report_key)
    selector_sql = "id=%s" if report_id is not None else "report_key=%s"
    selector_value = report_id if report_id is not None else report_key
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select id,report_key,report_date,report_type,title,summary,
                           school_scope,document_links,status,published_at,created_at,updated_at
                    from {t('past_reports')}
                    where district_id=%s and {selector_sql}""",
                (did, selector_value),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError("unknown report for district")
            return dict(row)


def require_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LIST:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIST}")
    return value


def list_reports(slug: str, limit: int, status: str | None = None) -> dict:
    limit = require_limit(limit)
    if status is not None and status not in REPORT_STATUSES:
        raise ValueError(f"status must be one of {sorted(REPORT_STATUSES)}")
    status_sql = " and status=%s" if status is not None else ""
    with connect() as conn:
        did = district_id(conn, slug)
        params = (did, status, limit) if status is not None else (did, limit)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select id,report_key,report_date,report_type,title,summary,
                           school_scope,document_links,status,published_at,created_at,updated_at
                    from {t('past_reports')}
                    where district_id=%s{status_sql}
                    order by report_date desc nulls last,id desc
                    limit %s""",
                params,
            )
            rows = [dict(row) for row in cur.fetchall()]
    return {"count": len(rows), "limit": limit, "reports": rows}


def positive_id(value: str) -> int:
    report_id = int(value)
    if report_id < 1:
        raise ValueError("report id must be positive")
    return report_id


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("--district", required=True)
    create.add_argument("file", type=Path)

    get = sub.add_parser("get")
    get.add_argument("--district", required=True)
    get_selector = get.add_mutually_exclusive_group(required=True)
    get_selector.add_argument("--id", type=positive_id)
    get_selector.add_argument("--report-key")

    listing = sub.add_parser("list")
    listing.add_argument("--district", required=True)
    listing.add_argument("--status", choices=sorted(REPORT_STATUSES))
    listing.add_argument("--limit", type=int, default=500)

    update = sub.add_parser("update")
    update.add_argument("--district", required=True)
    update_selector = update.add_mutually_exclusive_group(required=True)
    update_selector.add_argument("--id", type=positive_id)
    update_selector.add_argument("--report-key")
    update.add_argument("file", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_report(args.district, read_json(args.file))
        elif args.command == "get":
            result = get_report(args.district, args.id, args.report_key)
        elif args.command == "list":
            result = list_reports(args.district, args.limit, args.status)
        else:
            result = update_report(
                args.district, read_json(args.file), args.id, args.report_key
            )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, default=str))
        sys.exit(1)
    print(json.dumps({"ok": True, "result": result}, default=str))


if __name__ == "__main__":
    main()
