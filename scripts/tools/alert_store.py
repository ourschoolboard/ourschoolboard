"""Narrow storage capability for source-backed alert-review results."""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
from db import connect, dict_cursor, load_env, t  # noqa: E402
from school_scope import normalize_school_scope  # noqa: E402

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOPICS = {"budget", "curriculum", "facilities", "policy", "risk", "staffing"}
LABELS = {"budget": "Financial", "curriculum": "Curriculum", "facilities": "Facilities",
          "policy": "Day-to-day", "risk": "High-risk", "staffing": "Admin hiring"}


def _authorize() -> None:
    load_env()
    environment = os.environ.get("APP_ENV")
    if environment == "staging":
        return
    if environment == "production" and os.environ.get("ALERT_REVIEW_ENABLED") == "true":
        return
    raise ValueError("alert review writes require staging or explicit production authorization")


def _clean(item: dict) -> dict:
    key = str(item.get("id") or item.get("alertKey") or "").strip()
    title = str(item.get("title") or "").strip()
    status = str(item.get("status") or "draft")
    severity = item.get("severity")
    topic = item.get("topic")
    if not key or not title:
        raise ValueError("each alert requires id and title")
    if status not in {"draft", "published", "withdrawn"}:
        raise ValueError("invalid alert status")
    if severity not in {None, "low", "medium", "high"}:
        raise ValueError("invalid alert severity")
    if status == "published" and severity == "low":
        raise ValueError("low-severity alerts must remain draft")
    if topic not in TOPICS:
        raise ValueError("invalid alert topic")
    event_date = item.get("date") or item.get("eventDate")
    if event_date:
        date.fromisoformat(str(event_date))
    source_url = str(item.get("sourceUrl") or "").strip()
    if status == "published":
        required = ("summary", "whyItMatters", "evidence", "categoryLabel", "sourceTitle", "sourceHost", "sourceType")
        if any(not str(item.get(field) or "").strip() for field in required):
            raise ValueError("published alert is missing required source-backed fields")
        if (urlparse(source_url).hostname or "").lower() != str(item["sourceHost"]).lower():
            raise ValueError("sourceHost must match sourceUrl")
    return {**item, "id": key, "title": title, "status": status, "severity": severity,
            "topic": topic, "date": event_date, "sourceUrl": source_url,
            "schoolScope": normalize_school_scope(item.get("schoolScope"), published=status == "published")}


def put_reviewed_alerts(run_id: int, slug: str, payload, usage: dict) -> dict:
    _authorize()
    if not SLUG.fullmatch(slug):
        raise ValueError("invalid district slug")
    items = payload if isinstance(payload, list) else payload.get("alerts", [payload])
    if not isinstance(items, list):
        raise ValueError("alerts must be an array")
    clean = [_clean(item) for item in items]
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(f"select id from {t('districts')} where slug=%s", (slug,))
        district = cur.fetchone()
        if not district:
            raise ValueError(f"unknown district: {slug}")
        district_id = district["id"]
        keys = [item["id"] for item in clean]
        cur.execute(f"select alert_key,status from {t('alerts')} where district_id=%s and alert_key=any(%s) for update",
                    (district_id, keys))
        previous = {row["alert_key"]: row["status"] for row in cur.fetchall()}
        stored, new_ids = [], []
        for item in clean:
            cur.execute(
                f"""select m.id from {t('agency_meetings')} m join {t('district_documents')} x on x.meeting_id=m.id
                      where x.district_id=%s and m.source_url=%s""", (district_id, item["sourceUrl"]))
            source = cur.fetchone()
            if item["status"] == "published" and not source:
                raise ValueError(f"published alert {item['id']} has no collected source")
            content = {key: value for key, value in item.items()
                       if key not in {"needles", "verifiedPhrases"} and not key.startswith("_source")}
            cur.execute(
                f"""insert into {t('alerts')}
                      (district_id,alert_key,title,summary,topic,severity,event_date,source_url,
                       source_meeting_id,school_scope,content,status,published_at,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                      case when %s='published' then now() end,now())
                    on conflict (district_id,alert_key) do update set
                      title=excluded.title,summary=excluded.summary,topic=excluded.topic,severity=excluded.severity,
                      event_date=excluded.event_date,source_url=excluded.source_url,
                      source_meeting_id=excluded.source_meeting_id,school_scope=excluded.school_scope,
                      content=excluded.content,status=excluded.status,
                      published_at=case when excluded.status='published'
                        then coalesce({t('alerts')}.published_at,now()) else null end,updated_at=now()
                    returning id,alert_key,status,updated_at""",
                (district_id, item["id"], item["title"], item.get("summary"), item["topic"],
                 item["severity"], item["date"], item["sourceUrl"], source["id"] if source else None,
                 json.dumps(item["schoolScope"]), json.dumps(content), item["status"], item["status"]))
            row = dict(cur.fetchone())
            stored.append(row)
            if item["status"] == "published" and previous.get(item["id"]) != "published":
                new_ids.append(row["id"])
        cur.execute(
            f"""update {t('agency_scrape_runs')} set alert_review_status='notify_pending',
                      alert_review_error=null,alerts_published=%s,alert_review_alert_ids=%s,
                      alert_review_usage=%s::jsonb
                  where id=%s and district_id=%s and alert_review_status='reviewing' returning id""",
            (len(stored), list(dict.fromkeys(new_ids)), json.dumps(usage), run_id, district_id))
        if not cur.fetchone():
            raise ValueError(f"scrape run {run_id} is not a claimed review for {slug}")
    return {"stored": stored, "newAlertIds": list(dict.fromkeys(new_ids))}
