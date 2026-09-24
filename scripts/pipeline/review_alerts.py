#!/usr/bin/env python3
"""Review newly collected official documents and publish strictly verified alerts."""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, load_env, t  # noqa: E402
from alert_store import put_reviewed_alerts  # noqa: E402
from source_text import extract  # noqa: E402
from spaces_store import get_file  # noqa: E402

CATEGORY_LABELS = {
    "budget": "Financial", "curriculum": "Curriculum", "facilities": "Facilities",
    "policy": "Day-to-day", "risk": "High-risk", "staffing": "Admin hiring",
}

TOOL = {
    "name": "publish_alert_candidates",
    "description": "Return only consequential, source-supported school-board alerts.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"alerts": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "documentId": {"type": "string"},
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "whyItMatters": {"type": "string"},
                "evidence": {"type": "string"},
                "topic": {"type": "string", "enum": list(CATEGORY_LABELS)},
                "severity": {"type": "string", "enum": ["medium", "high"]},
                "schoolScope": {
                    "type": "array", "items": {"type": "string"}, "minItems": 1,
                },
            },
            "required": ["documentId", "slug", "title", "summary", "whyItMatters",
                         "evidence", "topic", "severity", "schoolScope"],
            "additionalProperties": False,
        }}},
        "required": ["alerts"],
        "additionalProperties": False,
    },
}

LIST_PAST_ALERTS_TOOL = {
    "name": "list_past_alerts",
    "description": (
        "List a bounded set of the district's published alert names and identifying context. "
        "Use query to search older alerts before publishing a potentially overlapping item."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 25},
        },
        "additionalProperties": False,
    },
}

GET_OVERVIEW_TOOL = {
    "name": "get_school_overview",
    "description": (
        "Read the district or school's currently published overview page content "
        "in a bounded chunk, or use query for matching passages."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "maxChars": {"type": "integer", "minimum": 500, "maximum": 8000},
        },
        "additionalProperties": False,
    },
}

GET_ALERT_DETAIL_TOOL = {
    "name": "get_alert_detail",
    "description": "Read the complete stored detail for one published district alert.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"alertKey": {"type": "string"}},
        "required": ["alertKey"],
        "additionalProperties": False,
    },
}

SEARCH_PAST_DOCUMENTS_TOOL = {
    "name": "search_past_documents",
    "description": (
        "Full-text search the complete extracted bodies of this district's current and past "
        "documents. Returns ranked matching passages and document IDs; use read_document_content "
        "to inspect more surrounding text."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

READ_DOCUMENT_CONTENT_TOOL = {
    "name": "read_document_content",
    "description": (
        "Read more of a district document's complete extracted text. Supply query to return "
        "matching windows from anywhere in the document, or offset to page through bounded chunks."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "documentId": {"type": "string"},
            "query": {"type": "string"},
            "offset": {"type": "integer", "minimum": 0},
            "maxChars": {"type": "integer", "minimum": 500, "maximum": 12000},
        },
        "required": ["documentId"],
        "additionalProperties": False,
    },
}

MODEL_TOOLS = [
    LIST_PAST_ALERTS_TOOL, GET_ALERT_DETAIL_TOOL, GET_OVERVIEW_TOOL,
    SEARCH_PAST_DOCUMENTS_TOOL, READ_DOCUMENT_CONTENT_TOOL, TOOL,
]

SYSTEM = """You review a chronological batch of newly collected official school-board records.
The user message gives the current UTC date, the scrape being reviewed, and the previous successful
scrape. These timestamps are context, not an age cutoff: do not automatically exclude an otherwise
consequential development solely because its source date is old. You MUST call list_past_alerts before
publishing candidates and must not repeat a development already represented there. If its result says
more alerts exist than were returned, use targeted list_past_alerts queries for likely overlaps. You may call
get_alert_detail to inspect a potentially overlapping alert, get_school_overview when published school
or district context would help, and search_past_documents plus read_document_content to investigate
historical context or text beyond the compact previews initially supplied. Prefer narrow searches and
small targeted reads, refining the query or offset when necessary. Use no more than fifteen optional read
calls in a batch, then publish your best source-supported result.
Search historical documents whenever a current record reports consequences without their underlying
figures or refers to an earlier decision. Use the historical source to recover the concrete figures,
decision context, and qualifications needed for a complete alert.
Return an alert only for a consequential development: a major budget change; a consequential course,
curriculum, instructional-program, or learning-material change; a facilities project, closure, or
capacity decision; a family-facing operating-policy or schedule change; a documented material
safety/legal/operational risk; or the hiring/departure/role change of a superintendent, principal, or
vice principal. Exclude routine purchases, warrants, ordinary staffing, ceremonies, and duplicate
items. Also exclude ceremonial naming, routine easements, and procedural comment-period notices unless
they reveal a material underlying risk or remediation. Do not alert on routine facilities maintenance,
repairs, or like-for-like replacements solely because the equipment has a safety function, including
fire-alarm replacements. A facilities item is alert-worthy only when the record establishes an emergency,
a concrete safety risk, a material closure or service disruption, a major capacity decision, or a
consequential new construction or renovation project. Distinguish proposals in agendas from completed
decisions in approved minutes. When an agenda and minutes describe the same action, return only the
completed outcome from the minutes. Every alert must
have distinct, plain-language what happened (summary), why it matters, and evidence fields. Keep the
summary to two concise sentences and normally no more than 70 words: state the development, then add
one context sentence answering the most important obvious follow-up with concrete source-backed
specifics. For example, say what a policy changes, what conduct or process a complaint concerns, what
conditions or deadline an approval imposes, or what a material budget amount funds. Use a targeted
document read when the preview only names an attachment or result; if the record cannot support that
one context sentence, do not publish an alert that merely paraphrases its title. One official source is sufficient. An agenda or official item
description may support an alert, but label
it clearly as proposed, planned, scheduled, or under consideration unless the supplied record establishes
the outcome. Missing attachments do not invalidate a sufficiently descriptive official item. Use only
the supplied document IDs and do not present unsupported inference as fact. For schoolScope, use exact
official school names affected by the action. Reserve "all schools" for structurally districtwide
actions such as the operating budget, superintendent hiring, district calendar, or a universal policy,
or cases where you are absolutely unsure what school is affected.

Severity:
- `high`: Immediate or incredibly large impact (e.g. evidence of financial fraud, principal or superintendent resignation, pressing safety risk, major change to curriculum/staffing/discipline, a major blow to student or parent rights, a massive update in a large-scale renovation project)
- `medium`: Very important, but will not overhaul the day to day life of students (e.g. new AI use policy, a new facility being built, a status update in a superintendent search)
- `low`: Noteworthy but not mission critical change (e.g. less than 5% of budget allocated to a project, a run of the mill bond issuance, an update on a well-known ongoing project like a charter school opening)

When the evidence establishes that
every school at a grade level is affected, use "all_elementary", "all_middle", or "all_high" in the
schoolScope array; combine those values when an action crosses levels. Empty alerts is valid."""

INITIAL_DOCUMENT_PREVIEW_CHARS = 1_200
MAX_BATCH_CHARS = 30_000
DEFAULT_DOCUMENT_READ_CHARS = 4_000
MAX_DOCUMENT_READ_CHARS = 12_000
MAX_OPTIONAL_READS = 15
MAX_MODEL_TURNS = 17
DEFAULT_REVIEW_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "medium"
EXTRACTION_TIMEOUT_SECONDS = 30


def extract_bounded(path: Path, timeout: int = EXTRACTION_TIMEOUT_SECONDS) -> str:
    """Prevent one malformed source from blocking an entire corpus review."""
    def timed_out(_signum, _frame):
        raise TimeoutError(f"source extraction exceeded {timeout} seconds")

    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(timeout)
    try:
        return extract(path)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def batch_documents(documents: list[dict], limit: int = MAX_BATCH_CHARS) -> list[list[dict]]:
    """Keep same-date records together while bounding model context."""
    groups: list[list[dict]] = []
    by_date: dict[str, list[dict]] = defaultdict(list)
    for doc in documents:
        by_date[str(doc.get("meeting_date") or "")].append(doc)
    for meeting_date in sorted(by_date):
        groups.append(by_date[meeting_date])
    batches: list[list[dict]] = []
    current: list[dict] = []
    chars = 0
    for group in groups:
        size = sum(min(len(doc.get("text") or ""), INITIAL_DOCUMENT_PREVIEW_CHARS) for doc in group)
        if size > limit:
            if current:
                batches.append(current)
                current, chars = [], 0
            chunk: list[dict] = []
            chunk_chars = 0
            for doc in group:
                doc_chars = min(len(doc.get("text") or ""), INITIAL_DOCUMENT_PREVIEW_CHARS)
                if chunk and chunk_chars + doc_chars > limit:
                    batches.append(chunk)
                    chunk, chunk_chars = [], 0
                chunk.append(doc)
                chunk_chars += doc_chars
            if chunk:
                batches.append(chunk)
            continue
        if current and chars + size > limit:
            batches.append(current)
            current, chars = [], 0
        current.extend(group)
        chars += size
    if current:
        batches.append(current)
    return batches


def _title_tokens(value: str) -> set[str]:
    ignored = {"board", "district", "school", "proposed", "proposal", "consider",
               "consent", "approved", "approves", "adopted", "adoption", "agenda"}
    return {token for token in re.findall(r"[a-z0-9]+", (value or "").lower())
            if len(token) > 2 and token not in ignored}


def _outcome_score(candidate: dict, document: dict) -> int:
    text = f"{candidate.get('title', '')} {candidate.get('summary', '')}".lower()
    score = 3 if document.get("kind") == "minutes" else 0
    if re.search(r"\b(approved|adopted|appointed|hired|selected|authorized)\b", text):
        score += 2
    if re.search(r"\b(proposed|proposal|consider|scheduled|would|may)\b", text):
        score -= 1
    return score


def dedupe_candidates(candidates: list[dict], documents_by_id: dict[str, dict]) -> list[dict]:
    """Prefer outcome records over same-date/topic duplicate proposals."""
    kept: list[dict] = []
    for candidate in candidates:
        document = documents_by_id.get(str(candidate.get("documentId")))
        if not document:
            continue
        tokens = _title_tokens(candidate.get("title", ""))
        duplicate_at = None
        for index, existing in enumerate(kept):
            other = documents_by_id.get(str(existing.get("documentId")))
            if not other or candidate.get("topic") != existing.get("topic"):
                continue
            if str(document.get("meeting_date")) != str(other.get("meeting_date")):
                continue
            other_tokens = _title_tokens(existing.get("title", ""))
            union = tokens | other_tokens
            similarity = len(tokens & other_tokens) / len(union) if union else 0
            if similarity >= 0.45:
                duplicate_at = index
                if _outcome_score(candidate, document) > _outcome_score(existing, other):
                    kept[index] = candidate
                break
        if duplicate_at is None:
            kept.append(candidate)
    return kept


def build_alerts(target: dict, candidates: list[dict],
                 documents_by_id: dict[str, dict]) -> list[dict]:
    """Build publishable alerts without inventing dates for undated records."""
    alerts = []
    for item in candidates:
        doc = documents_by_id.get(str(item.get("documentId")))
        if not doc or not doc.get("meeting_date"):
            continue
        topic = item.get("topic")
        slug = re.sub(r"[^a-z0-9]+", "-", str(item.get("slug") or "").lower()).strip("-")
        if topic not in CATEGORY_LABELS or not slug:
            continue
        meeting_date = str(doc["meeting_date"])
        alerts.append({
            "id": f"{target['slug']}-{meeting_date}-{slug}",
            "title": item["title"], "summary": item["summary"],
            "whyItMatters": item["whyItMatters"], "evidence": item["evidence"],
            "schoolScope": item["schoolScope"],
            "date": meeting_date, "topic": topic,
            "categoryLabel": CATEGORY_LABELS[topic], "severity": item["severity"],
            "sourceUrl": doc["source_url"], "sourceTitle": doc["title"],
            "sourceHost": urlparse(doc["source_url"]).hostname,
            "sourceType": {"agenda": "Board meeting agenda", "minutes": "Board meeting minutes",
                           "packet": "Board meeting packet",
                           "video": "Board meeting recording transcript"}.get(
                               doc["kind"], "Official district record"
                           ),
            "status": "published",
        })
    return alerts


def documents_for_run(run_id: int) -> tuple[dict, list[dict]]:
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""select r.id,r.district_id,r.started_at,r.finished_at,d.slug,d.name,
                       (select max(previous.finished_at)
                          from {t('agency_scrape_runs')} previous
                         where previous.district_id=r.district_id
                           and previous.status='ok' and previous.id<>r.id
                           and previous.finished_at<=r.started_at) as previous_scrape_at
                  from {t('agency_scrape_runs')} r
                  join {t('districts')} d on d.id=r.district_id where r.id=%s""", (run_id,)
        )
        target = cur.fetchone()
        if not target:
            raise ValueError(f"unknown scrape run {run_id}")
        target = dict(target)
        cur.execute(
            f"""select alert_key,title,event_date,topic,source_url
                  from {t('alerts')}
                 where district_id=%s and status='published'
                 order by event_date desc nulls last,published_at desc,id desc""",
            (target["district_id"],),
        )
        target["past_alerts"] = [dict(row) for row in cur.fetchall()]
        cur.execute(
            f"""select content from {t('district_pages')}
                 where district_id=%s and page_kind='overview' and status='published'""",
            (target["district_id"],),
        )
        overview = cur.fetchone()
        target["overview"] = overview["content"] if overview else None
        cur.execute(
            f"""select distinct m.id,m.meeting_date,m.title,m.kind,m.source_url,
                       v.id as document_version_id,v.storage_key,v.sha256,v.content_type
                  from {t('agency_document_versions')} v
                  join {t('agency_meetings')} m on m.id=v.meeting_id
                 where v.scrape_run_id=%s order by m.meeting_date,m.id""", (run_id,)
        )
        return target, [dict(row) for row in cur.fetchall()]


def _timestamp(value) -> str | None:
    return value.isoformat() if value is not None else None


def current_date_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def store_document_text(document: dict) -> None:
    """Persist full extracted text so future reviews can search the complete corpus."""
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""insert into {t('agency_document_texts')}
                       (document_version_id,meeting_id,content,extracted_at)
                 values (%s,%s,%s,now())
                 on conflict (document_version_id) do update
                   set content=excluded.content,extracted_at=now()""",
            (document["document_version_id"], document["id"], document.get("text") or ""),
        )


def get_alert_detail(district_id: int, alert_key: str) -> dict:
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""select alert_key,title,summary,topic,severity,event_date,source_url,
                       source_meeting_id,content,status,published_at
                  from {t('alerts')}
                 where district_id=%s and alert_key=%s and status='published'""",
            (district_id, alert_key),
        )
        row = cur.fetchone()
    return {"alert": dict(row) if row else None}


def search_past_documents(district_id: int, query: str, limit: int = 3) -> dict:
    query = (query or "").strip()[:300]
    if not query:
        return {"documents": []}
    limit = max(1, min(int(limit or 3), 5))
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""select m.id::text as document_id,m.meeting_date,
                       m.title,m.kind,m.source_url,
                       ts_headline('english',x.content,websearch_to_tsquery('english',%s),
                         'MaxFragments=2,MaxWords=30,MinWords=10') as matching_passages,
                       ts_rank(x.search_vector,websearch_to_tsquery('english',%s)) as rank
                  from {t('agency_meetings')} m
                  join lateral (
                    select content,search_vector
                      from {t('agency_document_texts')}
                     where meeting_id=m.id order by extracted_at desc limit 1
                  ) x on true
                 where m.district_id=%s
                   and x.search_vector @@ websearch_to_tsquery('english',%s)
                 order by rank desc,m.meeting_date desc nulls last
                 limit %s""",
            (query, query, district_id, query, limit),
        )
        rows = [dict(row) for row in cur.fetchall()]
    for row in rows:
        row.pop("rank", None)
    return {"documents": rows}


def _matching_windows(content: str, query: str, max_chars: int) -> list[dict]:
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower())))
    matches = sorted({match.start() for term in terms for match in re.finditer(re.escape(term), content.lower())})
    windows = []
    budget = max_chars
    for position in matches:
        start = max(0, position - 500)
        end = min(len(content), position + 1500, start + budget)
        if windows and start <= windows[-1]["end"]:
            continue
        windows.append({"start": start, "end": end, "text": content[start:end]})
        budget -= end - start
        if budget < 500:
            break
    return windows


def read_document_content(district_id: int, document_id: str, *, query: str = "",
                          offset: int = 0, max_chars: int = DEFAULT_DOCUMENT_READ_CHARS) -> dict:
    if not str(document_id).isdigit():
        return {"document": None}
    max_chars = max(500, min(int(max_chars or DEFAULT_DOCUMENT_READ_CHARS),
                             MAX_DOCUMENT_READ_CHARS))
    offset = max(0, int(offset or 0))
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""select m.id::text as document_id,m.meeting_date,m.title,m.kind,m.source_url,x.content
                  from {t('agency_document_texts')} x
                  join {t('agency_meetings')} m on m.id=x.meeting_id
                 where m.district_id=%s and m.id=%s
                 order by x.extracted_at desc limit 1""",
            (district_id, document_id),
        )
        row = cur.fetchone()
    if not row:
        return {"document": None}
    result = dict(row)
    content = result.pop("content") or ""
    result["totalChars"] = len(content)
    if query.strip():
        result["query"] = query
        result["matches"] = _matching_windows(content, query, max_chars)
    else:
        result["offset"] = offset
        result["text"] = content[offset:offset + max_chars]
        next_offset = offset + len(result["text"])
        result["nextOffset"] = next_offset if next_offset < len(content) else None
    return {"document": result}


def list_past_alerts(target: dict, *, query: str = "", limit: int = 20) -> dict:
    alerts = target.get("past_alerts") or []
    query_terms = re.findall(r"[a-z0-9]+", query.lower())
    if query_terms:
        alerts = [alert for alert in alerts if all(
            term in json.dumps(alert, default=str).lower() for term in query_terms
        )]
    limit = max(1, min(int(limit or 20), 25))
    return {"alerts": alerts[:limit], "total": len(alerts), "returned": min(len(alerts), limit)}


def read_overview(target: dict, *, query: str = "", offset: int = 0,
                  max_chars: int = 3000) -> dict:
    content = json.dumps(target.get("overview"), default=str, separators=(",", ":"))
    max_chars = max(500, min(int(max_chars or 3000), 8000))
    if query.strip():
        return {"totalChars": len(content), "query": query,
                "matches": _matching_windows(content, query, max_chars)}
    offset = max(0, int(offset or 0))
    text = content[offset:offset + max_chars]
    next_offset = offset + len(text)
    return {"totalChars": len(content), "offset": offset, "text": text,
            "nextOffset": next_offset if next_offset < len(content) else None}


def model_document_catalog(documents: list[dict]) -> list[dict]:
    return [{
        "id": str(doc["id"]), "date": str(doc["meeting_date"] or ""),
        "kind": doc["kind"], "title": doc["title"],
        "characters": len(doc.get("text") or ""),
        "preview": (doc.get("text") or "")[:INITIAL_DOCUMENT_PREVIEW_CHARS],
    } for doc in documents]


def execute_read_tool(target: dict, call: dict) -> dict:
    name = call.get("name")
    inputs = call.get("input") or {}
    if name == LIST_PAST_ALERTS_TOOL["name"]:
        return list_past_alerts(target, query=str(inputs.get("query") or ""),
                                limit=inputs.get("limit", 20))
    if name == GET_ALERT_DETAIL_TOOL["name"]:
        return get_alert_detail(target["district_id"], str(inputs.get("alertKey") or ""))
    if name == GET_OVERVIEW_TOOL["name"]:
        return read_overview(target, query=str(inputs.get("query") or ""),
                             offset=inputs.get("offset", 0),
                             max_chars=inputs.get("maxChars", 3000))
    if name == SEARCH_PAST_DOCUMENTS_TOOL["name"]:
        return search_past_documents(target["district_id"], str(inputs.get("query") or ""), inputs.get("limit", 3))
    if name == READ_DOCUMENT_CONTENT_TOOL["name"]:
        return read_document_content(
            target["district_id"], str(inputs.get("documentId") or ""),
            query=str(inputs.get("query") or ""), offset=inputs.get("offset", 0),
            max_chars=inputs.get("maxChars", DEFAULT_DOCUMENT_READ_CHARS),
        )
    raise RuntimeError(f"review model called unknown tool {name}")


def call_model(target: dict, documents: list[dict]) -> tuple[list[dict], dict]:
    payload_docs = model_document_catalog(documents)
    current_date = current_date_utc()
    next_input: str | list[dict] = json.dumps({
        "district": target["name"],
        "currentDateUtc": current_date,
        "scrapeStartedAt": _timestamp(target.get("started_at")),
        "scrapeFinishedAt": _timestamp(target.get("finished_at")),
        "previousSuccessfulScrapeAt": _timestamp(target.get("previous_scrape_at")),
        "documents": payload_docs,
    })
    usage = {"input_tokens": 0, "output_tokens": 0}
    headers = {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
               "content-type": "application/json"}
    tools = [{
        "type": "function", "name": tool["name"],
        "description": tool["description"], "parameters": tool["input_schema"],
    } for tool in MODEL_TOOLS]
    previous_response_id = None

    # The first turn is deliberately the read-only alert-list tool. Later turns
    # may read the overview or publish. Each HTTP request is attempted once.
    optional_reads = 0
    for turn in range(MAX_MODEL_TURNS):
        if turn == 0:
            tool_choice = {"type": "function", "name": LIST_PAST_ALERTS_TOOL["name"]}
        elif optional_reads >= MAX_OPTIONAL_READS or turn == MAX_MODEL_TURNS - 1:
            tool_choice = {"type": "function", "name": TOOL["name"]}
        else:
            tool_choice = "required"
        payload = {
            "model": os.environ.get("ALERT_REVIEW_MODEL", DEFAULT_REVIEW_MODEL),
            "reasoning": {"effort": os.environ.get(
                "ALERT_REVIEW_REASONING_EFFORT", DEFAULT_REASONING_EFFORT
            )},
            "instructions": SYSTEM, "input": next_input, "tools": tools,
            "tool_choice": tool_choice, "max_output_tokens": 5000, "store": True,
        }
        if previous_response_id:
            payload["previous_response_id"] = previous_response_id
        response = requests.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
            timeout=180,
        )
        response.raise_for_status()
        body = response.json()
        previous_response_id = body.get("id")
        if not previous_response_id:
            raise RuntimeError("review model response omitted its continuation id")
        for key in usage:
            usage[key] += int((body.get("usage") or {}).get(key, 0) or 0)
        blocks = [item for item in (body.get("output") or [])
                  if item.get("type") == "function_call"]
        published = next((item for item in blocks
                          if item.get("name") == TOOL["name"]), None)
        if published is not None and turn > 0:
            try:
                tool_input = json.loads(published.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("review model returned malformed publish JSON") from exc
            if not isinstance(tool_input, dict):
                raise RuntimeError(
                    "review model returned malformed publish input "
                    f"(type={type(tool_input).__name__})"
                )
            alerts = tool_input.get("alerts", [])
            if not isinstance(alerts, list):
                raise RuntimeError(
                    "review model returned a malformed alerts list "
                    f"(type={type(alerts).__name__}; "
                    f"input_keys={sorted(map(str, tool_input))})"
                )
            return [item for item in alerts if isinstance(item, dict)], usage

        if not blocks:
            raise RuntimeError("review model returned no structured tool call")
        results = []
        for call in blocks:
            try:
                inputs = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                inputs = {}
            normalized_call = {"name": call.get("name"), "input": inputs}
            is_optional = turn > 0 or call.get("name") != LIST_PAST_ALERTS_TOOL["name"]
            if is_optional and optional_reads >= MAX_OPTIONAL_READS:
                value = {"error": "optional read limit reached; publish the best supported result now"}
            else:
                value = execute_read_tool(target, normalized_call)
                optional_reads += int(is_optional)
            results.append({
                "type": "function_call_output", "call_id": call["call_id"],
                "output": json.dumps(value, default=str),
            })
        next_input = results
    raise RuntimeError("review model did not publish a structured result after tool use")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", type=int, required=True)
    args = parser.parse_args()
    load_env()
    environment = os.environ.get("APP_ENV")
    if environment not in {"staging", "production"}:
        raise RuntimeError(
            "review_alerts.py requires APP_ENV=staging or production"
        )
    if environment == "production" and os.environ.get("ALERT_REVIEW_ENABLED") != "true":
        raise RuntimeError("production alert review requires ALERT_REVIEW_ENABLED=true")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not configured")
    target, docs = documents_for_run(args.run_id)
    if not docs:
        result = put_reviewed_alerts(args.run_id, target["slug"], [], {})
        print(json.dumps({"ok": True, "runId": args.run_id, "documentsReviewed": 0,
                          "alertIds": result["newAlertIds"], "alerts": 0}))
        return

    usage = {"input_tokens": 0, "output_tokens": 0}
    candidates: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="osb-alert-review-") as temp:
        directory = Path(temp)
        for doc in docs:
            suffix = Path(urlparse(doc["source_url"]).path).suffix[:12] or ".bin"
            raw = directory / f"{doc['id']}{suffix}"
            get_file(doc["storage_key"], raw)
            try:
                doc["text"] = extract_bounded(raw)
            except Exception as exc:
                print(
                    f"warning: unreadable source {doc['id']}: {exc}",
                    file=sys.stderr,
                )
                doc["text"] = ""
            finally:
                # Reviews can contain thousands of large attachments. Keep the
                # extracted text in memory, but release each immutable source
                # copy before downloading the next one so /tmp stays bounded.
                raw.unlink(missing_ok=True)
            store_document_text(doc)

        for batch in batch_documents(docs):
            found, used = call_model(target, batch)
            candidates.extend(found)
            for key in usage:
                usage[key] += int(used.get(key, 0) or 0)

        by_id = {str(doc["id"]): doc for doc in docs}
        candidates = dedupe_candidates(candidates, by_id)
        alerts = build_alerts(target, candidates, by_id)
    result = put_reviewed_alerts(args.run_id, target["slug"], alerts, usage)
    stored = result["stored"]

    print(json.dumps({"ok": True, "runId": args.run_id, "documentsReviewed": len(docs),
                      "alerts": len(stored), "alertIds": result["newAlertIds"],
                      "usage": usage}))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
