#!/usr/bin/env python3
"""Write publishable district/page/alert content through a narrow DB tool.

This is the only interface the onboarding skill uses for page content. It owns
validation and the allowed upserts; it offers no arbitrary SQL capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, load_env, t  # noqa: E402
from school_scope import normalize_school_scope  # noqa: E402


def require_staging_env() -> None:
    """Refuse to write onboarded/generated content anywhere but staging.

    This public storage interface deliberately writes onboarding content only
    to staging. Production publication belongs in a separate, operator-owned
    promotion step.
    """
    load_env()
    if os.environ.get("APP_ENV") != "staging":
        raise ValueError(
            "refusing to write outside staging (APP_ENV=staging); "
            "production publication requires a separate promotion step"
        )


def require_alert_review_env() -> None:
    """Allow only staging or explicitly enabled production alert review writes."""
    load_env()
    environment = os.environ.get("APP_ENV")
    if environment == "staging":
        return
    if environment == "production" and os.environ.get("ALERT_REVIEW_ENABLED") == "true":
        return
    raise ValueError(
        "alert review writes require APP_ENV=staging or explicit "
        "APP_ENV=production with ALERT_REVIEW_ENABLED=true"
    )

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PAGE_KINDS = {"overview", "feed"}
ENTITY_TYPES = {"district", "school", "charter"}
STATUSES = {"draft", "published", "paused"}
ALERT_STATUSES = {"draft", "published", "withdrawn"}
SEVERITIES = {"low", "medium", "high"}
ALERT_TOPICS = {"budget", "curriculum", "facilities", "policy", "risk", "staffing"}
ALERT_TOPIC_LABELS = {
    "budget": "Financial", "curriculum": "Curriculum", "facilities": "Facilities",
    "policy": "Day-to-day", "risk": "High-risk", "staffing": "Admin hiring",
}
IMAGE_LICENSES = {"cc0", "public-domain", "by", "by-sa", "source-evidence"}
MAX_SOURCE_LIST = 10_000
MAX_RECONCILE_SOURCES = 500
MAX_DETACH_SOURCES = 500
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OVERVIEW_PIPELINE_LANGUAGE = re.compile(
    r"\b(?:scrap(?:e|er|ing)|collector|pipeline|corpus|idempoten\w*|repeat run|"
    r"parse error|fetch error)\b", re.IGNORECASE
)


def read_json(path: Path):
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("content file exceeds 2MB")
    return json.loads(path.read_text(encoding="utf-8"))


def require_slug(value: str) -> str:
    if not SLUG.fullmatch(value or ""):
        raise ValueError("slug must be lowercase words separated by hyphens")
    return value


def optional_text(value, field: str, maximum: int) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    clean = value.strip()
    if not clean:
        return None
    if len(clean) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return clean


def optional_url(value, field: str) -> str | None:
    clean = optional_text(value, field, 2048)
    if clean is None:
        return None
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field} must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{field} must not contain URL credentials")
    return clean


def normalize_page_image(value, field: str = "image") -> dict | None:
    """Validate reusable-image metadata kept with overview or alert content."""
    if value in (None, {}):
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    image = {
        "id": optional_text(value.get("id"), f"{field}.id", 500),
        "url": optional_url(value.get("url"), f"{field}.url"),
        "alt": optional_text(value.get("alt"), f"{field}.alt", 500),
        "attribution": optional_text(value.get("attribution"), f"{field}.attribution", 1000),
        "attributionUrl": optional_url(
            alias(value, "attributionUrl", "attribution_url"), f"{field}.attributionUrl"
        ),
        "license": optional_text(value.get("license"), f"{field}.license", 40),
        "licenseUrl": optional_url(
            alias(value, "licenseUrl", "license_url"), f"{field}.licenseUrl"
        ),
        "width": value.get("width"),
        "height": value.get("height"),
        "originalUrl": optional_url(
            alias(value, "originalUrl", "original_url"), f"{field}.originalUrl"
        ),
        "storageKey": optional_text(
            alias(value, "storageKey", "storage_key"), f"{field}.storageKey", 500
        ),
        "sha256": optional_text(value.get("sha256"), f"{field}.sha256", 64),
        "bytes": value.get("bytes"),
    }
    editorial = value.get("editorial")
    if editorial is not None:
        if not isinstance(editorial, dict):
            raise ValueError(f"{field}.editorial must be an object")
        image["editorial"] = {
            "kind": optional_text(editorial.get("kind"), f"{field}.editorial.kind", 80),
            "renderer": optional_text(
                editorial.get("renderer"), f"{field}.editorial.renderer", 120
            ),
            "version": optional_text(editorial.get("version"), f"{field}.editorial.version", 40),
            "baseImageId": optional_text(
                editorial.get("baseImageId"), f"{field}.editorial.baseImageId", 500
            ),
            "label": optional_text(editorial.get("label"), f"{field}.editorial.label", 100),
        }
        if not all(image["editorial"].values()):
            raise ValueError(f"{field}.editorial requires complete provenance")
        visual_sha256 = optional_text(
            editorial.get("visualSha256"), f"{field}.editorial.visualSha256", 64
        )
        if visual_sha256 is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", visual_sha256):
                raise ValueError(f"{field}.editorial.visualSha256 must be a SHA-256 digest")
            image["editorial"]["visualSha256"] = visual_sha256
    for required in ("id", "url", "alt", "attribution", "attributionUrl", "license"):
        if not image[required]:
            raise ValueError(f"{field} requires {required}")
    image["license"] = image["license"].lower()
    if image["license"] not in IMAGE_LICENSES:
        raise ValueError(f"{field}.license must be one of {sorted(IMAGE_LICENSES)}")
    if (image["license"] == "source-evidence"
            and (image.get("editorial") or {}).get("kind") != "source-evidence"):
        raise ValueError(f"{field}.license source-evidence requires source-evidence provenance")
    for dimension in ("width", "height", "bytes"):
        raw = image[dimension]
        if raw is not None and (isinstance(raw, bool) or not isinstance(raw, int) or raw < 1):
            raise ValueError(f"{field}.{dimension} must be a positive integer")
    return {key: val for key, val in image.items() if val is not None}


def assert_image_unused(cur, image: dict | None, *, district_id: int,
                        page_kind: str | None = None, alert_key: str | None = None) -> None:
    """One licensed image belongs to one public page, across pages and alerts."""
    if not image:
        return
    cur.execute(
        f"""select d.slug,p.page_kind from {t('district_pages')} p
              join {t('districts')} d on d.id=p.district_id
             where (p.content->'image'->>'id'=%s or p.content->'image'->>'url'=%s)
               and not (p.district_id=%s and p.page_kind=%s) limit 1""",
        (image["id"], image["url"], district_id, page_kind or ""),
    )
    used = cur.fetchone()
    if used:
        raise ValueError(f"image is already used by {used['slug']} {used['page_kind']} page")
    cur.execute(
        f"""select d.slug,a.alert_key from {t('alerts')} a
              join {t('districts')} d on d.id=a.district_id
             where (a.content->'image'->>'id'=%s or a.content->'image'->>'url'=%s)
               and not (a.district_id=%s and a.alert_key=%s) limit 1""",
        (image["id"], image["url"], district_id, alert_key or ""),
    )
    used = cur.fetchone()
    if used:
        raise ValueError(f"image is already used by {used['slug']} alert {used['alert_key']}")
    base_image_id = ((image.get("editorial") or {}).get("baseImageId"))
    if base_image_id:
        cur.execute(
            f"""select p.page_kind label from {t('district_pages')} p
                  where p.district_id=%s
                    and p.content->'image'->'editorial'->>'baseImageId'=%s
                    and p.page_kind<>%s
                 union all
                select a.alert_key label from {t('alerts')} a
                  where a.district_id=%s
                    and a.content->'image'->'editorial'->>'baseImageId'=%s
                    and a.alert_key<>%s
                 limit 1""",
            (district_id, base_image_id, page_kind or "",
             district_id, base_image_id, alert_key or ""),
        )
        used_base = cur.fetchone()
        if used_base:
            raise ValueError(
                f"fallback background is already used by this district: {used_base['label']}"
            )


def optional_email(value, field: str) -> str | None:
    clean = optional_text(value, field, 254)
    if clean is not None and not EMAIL.fullmatch(clean):
        raise ValueError(f"{field} must be an email address")
    return clean


def alias(item: dict, *names: str):
    for name in names:
        if name in item:
            return item[name]
    return None


SEAT_TYPES = {"ward", "at-large", "ex-officio", "appointed"}


def optional_seat_type(value, field: str) -> str | None:
    clean = optional_text(value, field, 40)
    if clean is not None and clean not in SEAT_TYPES:
        raise ValueError(f"{field} must be one of {sorted(SEAT_TYPES)}")
    return clean


def normalize_board_members(value) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("schoolBoardMembersList must be an array")
    if len(value) > 30:
        raise ValueError("schoolBoardMembersList cannot exceed 30 members")
    members = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"schoolBoardMembersList[{index}] must be an object")
        name = optional_text(item.get("name"), f"schoolBoardMembersList[{index}].name", 200)
        if not name:
            raise ValueError(f"schoolBoardMembersList[{index}].name is required")
        member = {
            "name": name,
            "role": optional_text(alias(item, "role", "title", "office"),
                                  f"schoolBoardMembersList[{index}].role", 200),
            "trusteeArea": optional_text(alias(item, "trusteeArea", "trustee_area", "districtArea", "district_area"),
                                         f"schoolBoardMembersList[{index}].trusteeArea", 200),
            "email": optional_email(item.get("email"), f"schoolBoardMembersList[{index}].email"),
            "phone": optional_text(item.get("phone"), f"schoolBoardMembersList[{index}].phone", 80),
            "termEnds": optional_text(alias(item, "termEnds", "term_ends"),
                                      f"schoolBoardMembersList[{index}].termEnds", 100),
            "seatType": optional_seat_type(alias(item, "seatType", "seat_type"),
                                           f"schoolBoardMembersList[{index}].seatType"),
            "firstElected": optional_text(alias(item, "firstElected", "first_elected"),
                                          f"schoolBoardMembersList[{index}].firstElected", 20),
            "background": optional_text(item.get("background"),
                                        f"schoolBoardMembersList[{index}].background", 500),
            "profileUrl": optional_url(alias(item, "profileUrl", "profile_url"),
                                       f"schoolBoardMembersList[{index}].profileUrl"),
            "sourceUrl": optional_url(alias(item, "sourceUrl", "source_url"),
                                      f"schoolBoardMembersList[{index}].sourceUrl"),
        }
        members.append({key: val for key, val in member.items() if val is not None})
    return members


def normalize_board_contact(value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("schoolBoardContactInfo must be an object")
    raw_emails = alias(value, "emails", "emailAddresses", "email_addresses")
    if raw_emails is None:
        raw_emails = []
    if not isinstance(raw_emails, list):
        raise ValueError("schoolBoardContactInfo.emails must be an array")
    if len(raw_emails) > 30:
        raise ValueError("schoolBoardContactInfo.emails cannot exceed 30 addresses")
    emails = []
    seen = set()
    for index, raw in enumerate(raw_emails):
        email = optional_email(raw, f"schoolBoardContactInfo.emails[{index}]")
        if email and email.lower() not in seen:
            seen.add(email.lower())
            emails.append(email)
    contact = {
        "emails": emails,
        "phone": optional_text(value.get("phone"), "schoolBoardContactInfo.phone", 80),
        "mailingAddress": optional_text(alias(value, "mailingAddress", "mailing_address"),
                                        "schoolBoardContactInfo.mailingAddress", 1000),
        "contactUrl": optional_url(alias(value, "contactUrl", "contact_url"),
                                   "schoolBoardContactInfo.contactUrl"),
        "sourceUrl": optional_url(alias(value, "sourceUrl", "source_url"),
                                  "schoolBoardContactInfo.sourceUrl"),
    }
    return {key: val for key, val in contact.items() if val not in (None, [])}


def put_district(payload: dict) -> dict:
    require_staging_env()
    slug = require_slug(str(payload.get("slug") or payload.get("id") or ""))
    name = str(payload.get("name") or "").strip()
    entity_type = str(payload.get("entityType") or payload.get("entity_type") or "district")
    status = str(payload.get("status") or "draft")
    if not name:
        raise ValueError("district name is required")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entityType must be one of {sorted(ENTITY_TYPES)}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    evidence = payload.get("sourceEvidence") or payload.get("source_evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("sourceEvidence must be an array")
    agency_leaid = payload.get("agencyLeaid") or payload.get("agency_leaid")
    members_present = "schoolBoardMembersList" in payload or "school_board_members_list" in payload
    contact_present = "schoolBoardContactInfo" in payload or "school_board_contact_info" in payload
    members = normalize_board_members(alias(
        payload, "schoolBoardMembersList", "school_board_members_list"
    ))
    contact = normalize_board_contact(alias(
        payload, "schoolBoardContactInfo", "school_board_contact_info"
    ))

    with connect() as conn:
        agency_id = None
        if agency_leaid:
            agency_name = str(payload.get("agencyName") or (name if entity_type == "district" else "")).strip()
            if not agency_name:
                raise ValueError("agencyName is required when onboarding a school whose governing LEA is new")
            with conn.cursor() as cur:
                cur.execute(
                    f"""insert into {t('agencies')}
                          (nces_leaid,name,state,website,status,updated_at)
                        values (%s,%s,%s,%s,'building',now())
                        on conflict (nces_leaid) do update set
                          name=excluded.name,
                          website=coalesce(excluded.website,{t('agencies')}.website),
                          updated_at=now()
                        returning id""",
                    (str(agency_leaid), agency_name, payload.get("state"),
                     payload.get("agencyWebsite") or payload.get("website")),
                )
                agency_id = cur.fetchone()[0]
        # The slug is chosen by whoever onboards; the NCES id is the identity.
        # Re-onboarding under a different slug convention used to insert a
        # second district rather than update the first, so the alerts stayed on
        # one row and the new page content went to the other. Adopt the
        # existing row's slug when its id already exists.
        nces_id = str(payload.get("ncesId") or payload.get("nces_id") or "").strip()
        if nces_id:
            with conn.cursor() as cur:
                cur.execute(
                    f"select slug from {t('districts')} where nces_id=%s and slug<>%s",
                    (nces_id, slug),
                )
                existing = cur.fetchone()
            if existing:
                print(
                    f"note: NCES {nces_id} is already district '{existing[0]}'; "
                    f"updating it instead of creating '{slug}'",
                    file=sys.stderr,
                )
                slug = existing[0]

        with dict_cursor(conn) as cur:
            cur.execute(
                f"""insert into {t('districts')}
                      (slug,name,state,entity_type,nces_id,agency_id,governed_by,website,
                       status,source_evidence,metadata,school_board_members_list,
                       school_board_contact_info,updated_at)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,
                            %s::jsonb,%s::jsonb,now())
                    on conflict (slug) do update set
                      name=excluded.name, state=excluded.state,
                      entity_type=excluded.entity_type, nces_id=excluded.nces_id,
                      agency_id=coalesce(excluded.agency_id,{t('districts')}.agency_id),
                      governed_by=excluded.governed_by, website=excluded.website,
                      status=excluded.status, source_evidence=excluded.source_evidence,
                      metadata=excluded.metadata,
                      school_board_members_list=case when %s
                        then excluded.school_board_members_list
                        else {t('districts')}.school_board_members_list end,
                      school_board_contact_info=case when %s
                        then excluded.school_board_contact_info
                        else {t('districts')}.school_board_contact_info end,
                      updated_at=now()
                    returning id,slug,name,status""",
                (slug, name, payload.get("state"), entity_type, nces_id or None,
                 agency_id, payload.get("governedBy"), payload.get("website"), status,
                 json.dumps(evidence), json.dumps(payload.get("metadata") or {}),
                 json.dumps(members), json.dumps(contact), members_present, contact_present),
            )
            return dict(cur.fetchone())


def district_id(conn, slug: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select id from {t('districts')} where slug=%s", (require_slug(slug),))
        row = cur.fetchone()
    if not row:
        raise ValueError(f"unknown district: {slug}")
    return row[0]


def require_source_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("source limit must be an integer")
    if not 1 <= value <= MAX_SOURCE_LIST:
        raise ValueError(f"source limit must be between 1 and {MAX_SOURCE_LIST}")
    return value


DRIVE_CANONICAL = re.compile(r"^https://drive\.google\.com/file/d/([^/?#]+)/view$")


def normalize_drive_reconciliation(payload: dict) -> tuple[list[dict], str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("logicalSources"), list):
        raise ValueError("reconciliation manifest requires a logicalSources array")
    raw_items = payload["logicalSources"]
    if not raw_items or len(raw_items) > MAX_RECONCILE_SOURCES:
        raise ValueError(
            f"logicalSources must contain between 1 and {MAX_RECONCILE_SOURCES} entries"
        )
    items = []
    seen_canonical = set()
    seen_binary = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"logicalSources[{index}] must be an object")
        canonical_url = optional_url(
            raw.get("canonicalUrl"), f"logicalSources[{index}].canonicalUrl"
        )
        match = DRIVE_CANONICAL.fullmatch(canonical_url or "")
        if not match:
            raise ValueError(f"logicalSources[{index}].canonicalUrl must be a canonical Drive view URL")
        drive_id = match.group(1)
        if canonical_url in seen_canonical:
            raise ValueError(f"duplicate canonicalUrl: {canonical_url}")
        seen_canonical.add(canonical_url)
        drop_shell = raw.get("dropShellOnly") is True
        binary_url = optional_url(
            raw.get("currentBinaryUrl"), f"logicalSources[{index}].currentBinaryUrl"
        )
        if drop_shell == bool(binary_url):
            raise ValueError(
                f"logicalSources[{index}] must provide exactly one of currentBinaryUrl or dropShellOnly"
            )
        item = {
            "canonicalUrl": canonical_url,
            "driveId": drive_id,
            "title": require_text(raw, "title", f"logicalSources[{index}]"),
            "kind": require_text(raw, "kind", f"logicalSources[{index}]"),
            "meetingDate": raw.get("meetingDate"),
            "dropShellOnly": drop_shell,
        }
        if item["meetingDate"] not in (None, "MISSING"):
            date.fromisoformat(str(item["meetingDate"]))
        if item["meetingDate"] == "MISSING":
            item["meetingDate"] = None
        if drop_shell:
            item["expectedShellSha256"] = require_text(
                raw, "expectedShellSha256", f"logicalSources[{index}]"
            )
            item["expectedShellStorageKey"] = require_text(
                raw, "expectedShellStorageKey", f"logicalSources[{index}]"
            )
        else:
            parsed = urlparse(binary_url)
            query = parse_qs(parsed.query)
            if (parsed.scheme != "https" or parsed.hostname != "drive.usercontent.google.com"
                    or parsed.path != "/download" or query.get("id") != [drive_id]):
                raise ValueError(
                    f"logicalSources[{index}].currentBinaryUrl must be the matching Drive download URL"
                )
            if binary_url in seen_binary:
                raise ValueError(f"duplicate currentBinaryUrl: {binary_url}")
            seen_binary.add(binary_url)
            item.update({
                "currentBinaryUrl": binary_url,
                "expectedBinarySha256": require_text(
                    raw, "expectedBinarySha256", f"logicalSources[{index}]"
                ),
                "expectedBinaryStorageKey": require_text(
                    raw, "expectedBinaryStorageKey", f"logicalSources[{index}]"
                ),
            })
            if raw.get("dateResolution") is not None:
                resolution = raw["dateResolution"]
                if not isinstance(resolution, dict) or not str(resolution.get("reason") or "").strip():
                    raise ValueError(f"logicalSources[{index}].dateResolution requires a reason")
                item["dateResolution"] = resolution
        items.append(item)
    items.sort(key=lambda item: item["canonicalUrl"])
    serialized = json.dumps({"logicalSources": items}, sort_keys=True, separators=(",", ":"))
    return items, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def reconcile_drive_sources(slug: str, payload: dict, *, apply: bool, confirm: str | None) -> dict:
    require_staging_env()
    items, digest = normalize_drive_reconciliation(payload)
    confirmation = f"RECONCILE {require_slug(slug)} {digest}"
    if apply and confirm != confirmation:
        raise ValueError(f"apply requires exact confirmation: {confirmation}")
    urls = [item["canonicalUrl"] for item in items]
    urls.extend(item["currentBinaryUrl"] for item in items if not item["dropShellOnly"])
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select m.id,m.agency_id,m.source_url,m.meeting_date,m.title,m.kind,
                           m.storage_key,m.sha256,m.content_type,m.bytes
                      from {t('agency_meetings')} m
                      join {t('district_documents')} x on x.meeting_id=m.id
                     where x.district_id=%s and m.source_url=any(%s)
                     for update""",
                (did, urls),
            )
            meetings = {row["source_url"]: dict(row) for row in cur.fetchall()}
            missing = sorted(set(urls) - set(meetings))
            if missing:
                raise ValueError("manifest sources are not associated with district: " + ", ".join(missing))
            meeting_ids = [row["id"] for row in meetings.values()]
            cur.execute(
                f"""select m.source_url,d.slug
                      from {t('district_documents')} x
                      join {t('districts')} d on d.id=x.district_id
                      join {t('agency_meetings')} m on m.id=x.meeting_id
                     where x.meeting_id=any(%s) and x.district_id<>%s""",
                (meeting_ids, did),
            )
            if cur.fetchall():
                raise ValueError("source reconciliation refuses meetings shared with another district")
            cur.execute(
                f"select source_url from {t('alerts')} where source_meeting_id=any(%s)",
                (meeting_ids,),
            )
            alert_refs = [row["source_url"] for row in cur.fetchall()]
            cur.execute(
                f"""select distinct link->>'url' as source_url
                      from {t('past_reports')} r,
                           jsonb_array_elements(r.document_links) link
                     where r.district_id=%s and link->>'url'=any(%s)""",
                (did, urls),
            )
            report_refs = [row["source_url"] for row in cur.fetchall()]
            if alert_refs or report_refs:
                raise ValueError("source reconciliation refuses sources already cited by alerts or reports")

            actions = []
            for item in items:
                shell = meetings[item["canonicalUrl"]]
                if shell["content_type"] != "text/html":
                    raise ValueError(f"canonical source is not an HTML shell: {item['canonicalUrl']}")
                if shell["title"] != item["title"] or shell["kind"] != item["kind"]:
                    raise ValueError(f"canonical source metadata changed: {item['canonicalUrl']}")
                if item["dropShellOnly"]:
                    if (shell["sha256"] != item["expectedShellSha256"]
                            or shell["storage_key"] != item["expectedShellStorageKey"]):
                        raise ValueError(f"shell source bytes changed: {item['canonicalUrl']}")
                    actions.append({"action": "dropShellOnly", "canonicalUrl": item["canonicalUrl"]})
                    continue
                binary = meetings[item["currentBinaryUrl"]]
                if shell["agency_id"] != binary["agency_id"]:
                    raise ValueError(f"canonical and binary source agencies differ: {item['canonicalUrl']}")
                if (binary["sha256"] != item["expectedBinarySha256"]
                        or binary["storage_key"] != item["expectedBinaryStorageKey"]):
                    raise ValueError(f"binary source bytes changed: {item['currentBinaryUrl']}")
                if binary["title"] != item["title"] or binary["kind"] != item["kind"]:
                    raise ValueError(f"binary source metadata changed: {item['currentBinaryUrl']}")
                shell_date = shell["meeting_date"].isoformat() if shell["meeting_date"] else None
                binary_date = binary["meeting_date"].isoformat() if binary["meeting_date"] else None
                if shell_date != binary_date and "dateResolution" not in item:
                    raise ValueError(f"source dates differ without dateResolution: {item['canonicalUrl']}")
                actions.append({
                    "action": "repointBinary", "canonicalUrl": item["canonicalUrl"],
                    "currentBinaryUrl": item["currentBinaryUrl"], "sha256": binary["sha256"],
                    "resolvedDate": item["meetingDate"],
                })

            if not apply:
                return {
                    "dryRun": True, "digest": digest, "confirmation": confirmation,
                    "beforeAssociations": len(meetings), "actions": actions,
                    "storageObjectsDeleted": 0,
                }

            for item in items:
                shell = meetings[item["canonicalUrl"]]
                cur.execute(
                    f"delete from {t('district_documents')} where district_id=%s and meeting_id=%s",
                    (did, shell["id"]),
                )
                cur.execute(f"delete from {t('agency_meetings')} where id=%s", (shell["id"],))
                if not item["dropShellOnly"]:
                    binary = meetings[item["currentBinaryUrl"]]
                    cur.execute(
                        f"""update {t('agency_meetings')}
                               set source_url=%s,meeting_date=%s,title=%s,kind=%s,updated_at=now()
                             where id=%s""",
                        (item["canonicalUrl"], item["meetingDate"], item["title"],
                         item["kind"], binary["id"]),
                    )
            audit = {
                "digest": digest, "appliedAt": datetime.now(timezone.utc).isoformat(),
                "repointed": sum(not item["dropShellOnly"] for item in items),
                "shellOnlyDropped": sum(item["dropShellOnly"] for item in items),
                "storageObjectsDeleted": 0,
            }
            cur.execute(
                f"""update {t('districts')}
                       set metadata=jsonb_set(coalesce(metadata,'{{}}'::jsonb),
                         '{{sourceReconciliation}}',%s::jsonb,true),updated_at=now()
                     where id=%s""",
                (json.dumps(audit), did),
            )
            cur.execute(
                f"select count(*)::int as count from {t('district_documents')} where district_id=%s",
                (did,),
            )
            after = cur.fetchone()["count"]
    return {
        "dryRun": False, "digest": digest, "beforeAssociations": len(meetings),
        "afterAssociations": after, "repointed": audit["repointed"],
        "shellOnlyDropped": audit["shellOnlyDropped"], "storageObjectsDeleted": 0,
    }


def list_sources(slug: str, limit: int) -> dict:
    """Enumerate a district's collected logical records without arbitrary SQL."""
    limit = require_source_limit(limit)
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select m.source_url,m.meeting_date,m.title,m.kind,m.storage_key,
                           m.sha256,m.content_type,m.bytes,m.created_at,
                           count(v.id)::integer as version_count,
                           max(v.fetched_at) as latest_fetched_at
                    from {t('agency_meetings')} m
                    join {t('district_documents')} x on x.meeting_id=m.id
                    left join {t('agency_document_versions')} v on v.meeting_id=m.id
                    where x.district_id=%s
                    group by m.id,m.source_url,m.meeting_date,m.title,m.kind,
                             m.storage_key,m.sha256,m.content_type,m.bytes,m.created_at
                    order by m.meeting_date asc nulls last,m.created_at asc,m.id asc
                    limit %s""",
                (did, limit),
            )
            sources = [dict(row) for row in cur.fetchall()]
    return {"count": len(sources), "limit": limit, "sources": sources}


def normalize_source_detachment(payload: dict) -> tuple[list[dict], str]:
    raw_items = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("source detachment requires a non-empty sources array")
    if len(raw_items) > MAX_DETACH_SOURCES:
        raise ValueError(f"source detachment exceeds {MAX_DETACH_SOURCES} sources")
    items = []
    seen = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"sources[{index}] must be an object")
        url = optional_url(raw.get("url"), f"sources[{index}].url")
        sha256 = require_text(raw, "sha256", f"sources[{index}]").lower()
        storage_key = require_text(raw, "storageKey", f"sources[{index}]")
        reason = require_text(raw, "reason", f"sources[{index}]")
        if not url or url in seen:
            raise ValueError(f"sources[{index}].url must be unique")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError(f"sources[{index}].sha256 must be lowercase hexadecimal")
        if len(reason) > 500:
            raise ValueError(f"sources[{index}].reason exceeds 500 characters")
        seen.add(url)
        items.append({"url": url, "sha256": sha256, "storageKey": storage_key,
                      "reason": reason})
    items.sort(key=lambda item: item["url"])
    serialized = json.dumps({"sources": items}, sort_keys=True, separators=(",", ":"))
    return items, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def detach_sources(slug: str, payload: dict, *, apply: bool, confirm: str | None) -> dict:
    """Detach exact, uncited historical mistakes without deleting source provenance."""
    require_staging_env()
    slug = require_slug(slug)
    items, digest = normalize_source_detachment(payload)
    confirmation = f"DETACH {slug} {digest}"
    if apply and confirm != confirmation:
        raise ValueError(f"apply requires exact confirmation: {confirmation}")
    urls = [item["url"] for item in items]
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select m.id,m.source_url,m.sha256,m.storage_key
                      from {t('agency_meetings')} m
                      join {t('district_documents')} x on x.meeting_id=m.id
                     where x.district_id=%s and m.source_url=any(%s) for update""",
                (did, urls),
            )
            rows = {row["source_url"]: dict(row) for row in cur.fetchall()}
            missing = sorted(set(urls) - set(rows))
            if missing:
                raise ValueError("manifest sources are not associated with district: " + ", ".join(missing))
            for item in items:
                row = rows[item["url"]]
                if row["sha256"] != item["sha256"] or row["storage_key"] != item["storageKey"]:
                    raise ValueError(f"source bytes changed: {item['url']}")
            ids = [row["id"] for row in rows.values()]
            cur.execute(
                f"select source_url from {t('alerts')} where district_id=%s and source_meeting_id=any(%s)",
                (did, ids),
            )
            cited = [row["source_url"] for row in cur.fetchall()]
            cur.execute(
                f"""select distinct link->>'url' as source_url
                      from {t('past_reports')} r,
                           jsonb_array_elements(r.document_links) link
                     where r.district_id=%s and link->>'url'=any(%s)""",
                (did, urls),
            )
            cited.extend(row["source_url"] for row in cur.fetchall())
            if cited:
                raise ValueError("source detachment refuses cited sources: " + ", ".join(sorted(set(cited))))
            actions = [{"url": item["url"], "reason": item["reason"]} for item in items]
            if not apply:
                return {"dryRun": True, "digest": digest, "confirmation": confirmation,
                        "beforeAssociations": len(rows), "actions": actions,
                        "storageObjectsDeleted": 0}
            cur.execute(
                f"delete from {t('district_documents')} where district_id=%s and meeting_id=any(%s)",
                (did, ids),
            )
            audit = {"digest": digest, "appliedAt": datetime.now(timezone.utc).isoformat(),
                     "detached": len(items), "storageObjectsDeleted": 0, "sources": actions}
            cur.execute(
                f"""update {t('districts')}
                       set metadata=jsonb_set(coalesce(metadata,'{{}}'::jsonb),
                         '{{sourceDetachment}}',%s::jsonb,true),updated_at=now()
                     where id=%s""",
                (json.dumps(audit), did),
            )
    return {"dryRun": False, "digest": digest, "detached": len(items),
            "storageObjectsDeleted": 0}


def normalize_source_identity_migration(payload: dict) -> tuple[list[dict], str]:
    """Validate an exact old-Drive-URL to canonical-view-URL manifest."""
    items = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items or len(items) > 100:
        raise ValueError("source identity migration requires 1-100 sources")
    normalized = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise ValueError(f"sources[{index}] must be an object")
        old = optional_url(raw.get("currentUrl"), f"sources[{index}].currentUrl")
        new = optional_url(raw.get("canonicalUrl"), f"sources[{index}].canonicalUrl")
        sha = require_text(raw, "sha256", f"sources[{index}]").lower()
        key = require_text(raw, "storageKey", f"sources[{index}]")
        title = require_text(raw, "title", f"sources[{index}]")
        kind = require_text(raw, "kind", f"sources[{index}]")
        if not old or not new or old == new or kind not in {"agenda", "minutes", "packet", "other"}:
            raise ValueError(f"sources[{index}] has invalid identity migration")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError(f"sources[{index}].sha256 must be lowercase hexadecimal")
        old_id = re.search(r"[?&]id=([A-Za-z0-9_-]{15,})", old)
        new_id = re.search(r"/file/d/([A-Za-z0-9_-]{15,})/view", new)
        if not old_id or not new_id or old_id.group(1) != new_id.group(1):
            raise ValueError(f"sources[{index}] Drive file IDs must match")
        normalized.append({"currentUrl": old, "canonicalUrl": new, "sha256": sha,
                           "storageKey": key, "title": title, "kind": kind})
    if len({x["currentUrl"] for x in normalized}) != len(normalized) or len({x["canonicalUrl"] for x in normalized}) != len(normalized):
        raise ValueError("source identity migration URLs must be unique")
    normalized.sort(key=lambda item: item["currentUrl"])
    digest = hashlib.sha256(
        json.dumps({"sources": normalized}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return normalized, digest


def migrate_source_identities(slug: str, payload: dict, *, apply: bool, confirm: str | None) -> dict:
    """Atomically replace exact uncited source URLs while retaining source bytes."""
    require_staging_env()
    normalized, digest = normalize_source_identity_migration(payload)
    expected = f"MIGRATE {require_slug(slug)} {digest}"
    if apply and confirm != expected:
        raise ValueError(f"apply requires exact confirmation: {expected}")
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            olds = [x["currentUrl"] for x in normalized]
            news = [x["canonicalUrl"] for x in normalized]
            cur.execute(f"""select m.id,m.source_url,m.sha256,m.storage_key from {t('agency_meetings')} m join {t('district_documents')} x on x.meeting_id=m.id where x.district_id=%s and m.source_url=any(%s) for update""", (did, olds))
            rows = {r["source_url"]: dict(r) for r in cur.fetchall()}
            if set(rows) != set(olds): raise ValueError("migration source is not associated with district")
            cur.execute(f"select source_url from {t('agency_meetings')} where source_url=any(%s)", (news,))
            if cur.fetchall(): raise ValueError("canonical URL already exists; use reconciliation instead")
            cur.execute(f"select source_url from {t('alerts')} where district_id=%s and source_meeting_id=any(%s)", (did, [r['id'] for r in rows.values()]))
            if cur.fetchall(): raise ValueError("migration refuses alert-cited sources")
            for item in normalized:
                row = rows[item["currentUrl"]]
                if row["sha256"] != item["sha256"] or row["storage_key"] != item["storageKey"]: raise ValueError("migration source bytes changed")
            if apply:
                for item in normalized:
                    cur.execute(f"update {t('agency_meetings')} set source_url=%s,title=%s,kind=%s where id=%s", (item["canonicalUrl"], item["title"], item["kind"], rows[item["currentUrl"]]["id"]))
    return {"dryRun": not apply, "digest": digest, "confirmation": expected, "migrated": len(normalized), "storageObjectsDeleted": 0}


def require_text(payload: dict, field: str, subject: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ValueError(f"{subject} requires {field}")
    return value


def validate_feed_content(content: dict) -> None:
    district = content.get("district")
    if not isinstance(district, dict):
        raise ValueError("a published feed requires a district object")
    require_text(district, "id", "a published feed district")
    require_text(district, "name", "a published feed district")

    period = content.get("period")
    if not isinstance(period, dict):
        raise ValueError("a published feed requires a period object")
    start = require_text(period, "start", "a published feed period")
    end = require_text(period, "end", "a published feed period")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("published feed period start must not be after end")

    summary = content.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("a published feed requires a structured summary object")
    if not any(type(summary.get(field)) is int and summary[field] >= 0 for field in
               ("documents", "documentsRead", "meetingsCovered", "alertsPublished")):
        raise ValueError("published feed summary requires at least one nonnegative integer count")

    categories = content.get("categories")
    if not isinstance(categories, list):
        raise ValueError("a published feed requires a categories array")
    for category in categories:
        if not isinstance(category, dict):
            raise ValueError("each published feed category must be an object")
        require_text(category, "id", "each published feed category")
        require_text(category, "label", "each published feed category")
        if type(category.get("count")) is not int or category["count"] < 0:
            raise ValueError("each published feed category requires a nonnegative count")

    if not isinstance(content.get("limitations"), list):
        raise ValueError("a published feed requires a limitations array")


def validate_overview_content(content: dict) -> None:
    normalize_page_image(content.get("image"))
    district = content.get("district")
    if not isinstance(district, dict):
        raise ValueError("a published overview requires a district object")
    require_text(district, "id", "a published overview district")
    require_text(district, "name", "a published overview district")

    structure = content.get("keyStructure")
    if not isinstance(structure, dict):
        raise ValueError("a published overview requires keyStructure")
    for field in ("navLabel", "heading", "status", "explanation"):
        require_text(structure, field, "a published overview keyStructure")
    points = structure.get("points")
    if not isinstance(points, list) or len(points) < 3:
        raise ValueError("a published overview requires at least three keyStructure points")
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("each overview keyStructure point must be an object")
        require_text(point, "point", "each overview keyStructure point")
        require_text(point, "detail", "each overview keyStructure point")

    metrics = content.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("a published overview requires at least one metric")
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ValueError("each overview metric must be an object")
        require_text(metric, "id", "each overview metric")
        require_text(metric, "label", "each overview metric")
        if metric.get("value") is None or isinstance(metric.get("value"), bool):
            raise ValueError("each overview metric requires a value")
        require_text(metric, "unit", "each overview metric")
        if metric.get("verified") is True:
            optional_url(require_text(metric, "sourceUrl", "each verified overview metric"),
                         "verified overview metric sourceUrl")
            require_text(metric, "sourceTitle", "each verified overview metric")

    projects = content.get("projects")
    if not isinstance(projects, list) or not projects:
        raise ValueError("a published overview requires at least one project")
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError("each overview project must be an object")
        for field in ("id", "name", "status", "summary", "nextMilestone"):
            require_text(project, field, "each overview project")
        timeline = project.get("timeline")
        if not isinstance(timeline, list) or not timeline:
            raise ValueError("each overview project requires a timeline")
        for event in timeline:
            if not isinstance(event, dict):
                raise ValueError("each overview project timeline event must be an object")
            date.fromisoformat(require_text(event, "date", "each overview project timeline event"))
            require_text(event, "label", "each overview project timeline event")
        budget = project.get("budget")
        if not isinstance(budget, dict):
            raise ValueError("each overview project requires budget funding context")
        require_text(budget, "fundingSource", "each overview project budget")

    require_text(content, "method", "a published overview")
    if not isinstance(content.get("limitations"), list):
        raise ValueError("a published overview requires a limitations array")
    editorial_core = json.dumps(
        {"keyStructure": structure, "metrics": metrics, "projects": projects}
    )
    if OVERVIEW_PIPELINE_LANGUAGE.search(editorial_core):
        raise ValueError("published overview core sections must describe the entity, not scraping operations")


def validate_category_content(content: dict) -> None:
    topic = content.get("topic")
    if topic not in ALERT_TOPICS:
        raise ValueError(f"a published category page topic must be one of {sorted(ALERT_TOPICS)}")
    require_text(content, "categoryLabel", "a published category page")
    require_text(content, "narrative", "a published category page")

    highlights = content.get("highlights")
    if not isinstance(highlights, list) or not highlights:
        raise ValueError("a published category page requires at least one highlight")
    for highlight in highlights:
        if not isinstance(highlight, dict):
            raise ValueError("each category page highlight must be an object")
        for field in ("title", "summary", "alertId"):
            require_text(highlight, field, "each category page highlight")

    require_text(content, "method", "a published category page")
    if not isinstance(content.get("limitations"), list):
        raise ValueError("a published category page requires a limitations array")


def put_category_page(slug: str, topic: str, content: dict, status: str,
                       alert_count: int, source_alert_ids: list[int]) -> dict:
    require_staging_env()
    if topic not in ALERT_TOPICS:
        raise ValueError(f"topic must be one of {sorted(ALERT_TOPICS)}")
    if status not in {"draft", "published"}:
        raise ValueError("category page status must be draft or published")
    if not isinstance(content, dict):
        raise ValueError("category page content must be a JSON object")
    if not isinstance(alert_count, int) or isinstance(alert_count, bool) or alert_count < 0:
        raise ValueError("alert_count must be a nonnegative integer")
    if not isinstance(source_alert_ids, list) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in source_alert_ids
    ):
        raise ValueError("source_alert_ids must be an array of integers")
    if status == "published":
        validate_category_content(content)
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""insert into {t('district_category_pages')}
                      (district_id,topic,content,alert_count,source_alert_ids,status,
                       generated_at,published_at,updated_at)
                    values (%s,%s,%s::jsonb,%s,%s,%s,now(),
                            case when %s='published' then now() end,now())
                    on conflict (district_id,topic) do update set
                      content=excluded.content, alert_count=excluded.alert_count,
                      source_alert_ids=excluded.source_alert_ids, status=excluded.status,
                      generated_at=now(),
                      published_at=case when excluded.status='published'
                        then coalesce({t('district_category_pages')}.published_at,now()) else null end,
                      updated_at=now()
                    returning id,topic,status,alert_count,updated_at""",
                (did, topic, json.dumps(content), alert_count, source_alert_ids, status, status),
            )
            return dict(cur.fetchone())


def put_page(slug: str, kind: str, content: dict, status: str) -> dict:
    require_staging_env()
    if kind not in PAGE_KINDS:
        raise ValueError(f"page kind must be one of {sorted(PAGE_KINDS)}")
    if status not in {"draft", "published"}:
        raise ValueError("page status must be draft or published")
    if not isinstance(content, dict):
        raise ValueError("page content must be a JSON object")
    if kind == "feed" and status == "published":
        validate_feed_content(content)
    if kind == "overview" and status == "published":
        validate_overview_content(content)
    with connect() as conn:
        did = district_id(conn, slug)
        with dict_cursor(conn) as cur:
            image = normalize_page_image(content.get("image"))
            if image:
                content = {**content, "image": image}
            assert_image_unused(cur, image, district_id=did, page_kind=kind)
            cur.execute(
                f"""insert into {t('district_pages')}
                      (district_id,page_kind,content,status,published_at,updated_at)
                    values (%s,%s,%s::jsonb,%s,case when %s='published' then now() end,now())
                    on conflict (district_id,page_kind) do update set
                      content=excluded.content, status=excluded.status,
                      published_at=case when excluded.status='published'
                        then coalesce({t('district_pages')}.published_at,now()) else null end,
                      updated_at=now()
                    returning id,page_kind,status,updated_at""",
                (did, kind, json.dumps(content), status, status),
            )
            return dict(cur.fetchone())


def normalize_alert(item: dict) -> dict:
    key = str(item.get("id") or item.get("alertKey") or "").strip()
    title = str(item.get("title") or "").strip()
    status = str(item.get("status") or "draft")
    severity = item.get("severity")
    if not key or not title:
        raise ValueError("each alert requires id and title")
    if status not in ALERT_STATUSES:
        raise ValueError(f"alert status must be one of {sorted(ALERT_STATUSES)}")
    if severity is not None and severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {sorted(SEVERITIES)}")
    if status == "published" and severity == "low":
        raise ValueError("low-severity alerts must remain draft")
    topic = item.get("topic")
    if topic is not None and topic not in ALERT_TOPICS:
        raise ValueError(f"topic must be one of {sorted(ALERT_TOPICS)}")
    if status == "published":
        for field in (
            "summary", "whyItMatters", "evidence", "categoryLabel",
            "sourceTitle", "sourceHost", "sourceType", "topic", "date",
        ):
            require_text(item, field, "a published alert")
        source_url = require_text(item, "sourceUrl", "a published alert")
        declared_host = require_text(item, "sourceHost", "a published alert")
        source_host = urlparse(source_url).hostname
        if not source_host or declared_host.lower() != source_host.lower():
            raise ValueError("published alert sourceHost must match sourceUrl")
    event_date = item.get("date") or item.get("eventDate")
    if event_date:
        date.fromisoformat(str(event_date))
    school_scope = normalize_school_scope(
        item.get("schoolScope", item.get("school_scope")),
        published=status == "published",
    )
    image = normalize_page_image(item.get("image"), "alert image")
    return {
        **item,
        "id": key,
        "title": title,
        "status": status,
        "date": event_date,
        "schoolScope": school_scope,
        **({"image": image} if image else {}),
    }


def source_backed_alert_content(item: dict, source: dict | None) -> dict:
    """Require a collected official source and omit legacy verification fields."""
    content = {
        key: value for key, value in item.items()
        if not key.startswith("_source") and key not in {"needles", "verifiedPhrases"}
    }
    if item["status"] != "published":
        return content
    if not source:
        raise ValueError(f"published alert {item['id']} does not resolve to a collected sourceUrl")
    return content


def put_alerts(slug: str, payload) -> list[dict]:
    require_staging_env()
    items = payload if isinstance(payload, list) else payload.get("alerts", [payload])
    if not isinstance(items, list) or not items:
        raise ValueError("alert file must contain an alert or non-empty alerts array")
    clean = [normalize_alert(item) for item in items]
    with connect() as conn:
        did = district_id(conn, slug)
        output = []
        with dict_cursor(conn) as cur:
            for item in clean:
                assert_image_unused(cur, item.get("image"), district_id=did,
                                    alert_key=item["id"])
                source_url = item.get("sourceUrl")
                source_meeting_id = None
                source = None
                if source_url:
                    cur.execute(
                        f"""select m.id,m.sha256,m.storage_key from {t('agency_meetings')} m
                            join {t('district_documents')} x on x.meeting_id=m.id
                            where x.district_id=%s and m.source_url=%s""",
                        (did, source_url),
                    )
                    source = cur.fetchone()
                    source_meeting_id = source["id"] if source else None
                content = source_backed_alert_content(item, source)
                cur.execute(
                    f"""insert into {t('alerts')}
                          (district_id,alert_key,title,summary,topic,severity,event_date,
                           source_url,source_meeting_id,school_scope,content,status,
                           published_at,updated_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                          case when %s='published' then now() end,now())
                        on conflict (district_id,alert_key) do update set
                          title=excluded.title, summary=excluded.summary, topic=excluded.topic,
                          severity=excluded.severity, event_date=excluded.event_date,
                          source_url=excluded.source_url,
                          source_meeting_id=excluded.source_meeting_id,
                          school_scope=excluded.school_scope,
                          content=excluded.content, status=excluded.status,
                          published_at=case when excluded.status='published'
                            then coalesce({t('alerts')}.published_at,now()) else null end,
                          updated_at=now()
                        returning id,alert_key,status,updated_at""",
                    (did, item["id"], item["title"], item.get("summary") or item.get("dek"),
                     item.get("topic"), item.get("severity"), item.get("date"), source_url,
                     source_meeting_id, json.dumps(item["schoolScope"]), json.dumps(content),
                     item["status"], item["status"]),
                )
                output.append(dict(cur.fetchone()))
        return output


def put_reviewed_alerts(run_id: int, slug: str, payload, usage: dict) -> dict:
    """Atomically publish a review result and make its notifications resumable.

    This is deliberately narrower than the general put_* surface: production
    authorization applies only to source-backed alerts generated for one
    claimed scrape run, plus derived feed counts and that run's review state.
    """
    require_alert_review_env()
    items = payload if isinstance(payload, list) else payload.get("alerts", [payload])
    if not isinstance(items, list):
        raise ValueError("alerts must be an array")
    clean = [normalize_alert(item) for item in items]
    with connect() as conn:
        did = district_id(conn, slug)
        stored = []
        newly_published_ids = []
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select alert_key,status from {t('alerts')}
                      where district_id=%s and alert_key=any(%s) for update""",
                (did, [item["id"] for item in clean]),
            )
            previous = {row["alert_key"]: row["status"] for row in cur.fetchall()}
            for item in clean:
                assert_image_unused(cur, item.get("image"), district_id=did,
                                    alert_key=item["id"])
                source_url = item.get("sourceUrl")
                source = None
                if source_url:
                    cur.execute(
                        f"""select m.id,m.sha256,m.storage_key from {t('agency_meetings')} m
                            join {t('district_documents')} x on x.meeting_id=m.id
                            where x.district_id=%s and m.source_url=%s""",
                        (did, source_url),
                    )
                    source = cur.fetchone()
                content = source_backed_alert_content(item, source)
                cur.execute(
                    f"""insert into {t('alerts')}
                          (district_id,alert_key,title,summary,topic,severity,event_date,
                           source_url,source_meeting_id,school_scope,content,status,
                           published_at,updated_at)
                        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,
                          case when %s='published' then now() end,now())
                        on conflict (district_id,alert_key) do update set
                          title=excluded.title, summary=excluded.summary, topic=excluded.topic,
                          severity=excluded.severity, event_date=excluded.event_date,
                          source_url=excluded.source_url,
                          source_meeting_id=excluded.source_meeting_id,
                          school_scope=excluded.school_scope,
                          content=excluded.content, status=excluded.status,
                          published_at=case when excluded.status='published'
                            then coalesce({t('alerts')}.published_at,now()) else null end,
                          updated_at=now()
                        returning id,alert_key,status,updated_at""",
                    (did, item["id"], item["title"], item.get("summary") or item.get("dek"),
                     item.get("topic"), item.get("severity"), item.get("date"), source_url,
                     source["id"] if source else None, json.dumps(item["schoolScope"]),
                     json.dumps(content), item["status"], item["status"]),
                )
                row = dict(cur.fetchone())
                stored.append(row)
                if item["status"] == "published" and previous.get(item["id"]) != "published":
                    newly_published_ids.append(row["id"])

            cur.execute(
                f"""select p.content,p.status
                      from {t('district_pages')} p
                     where p.district_id=%s and p.page_kind='feed' for update""",
                (did,),
            )
            feed = cur.fetchone()
            if feed and feed["status"] == "published":
                content = dict(feed["content"])
                cur.execute(
                    f"""select count(distinct v.sha256)::int as documents,
                               count(distinct m.meeting_date)::int as meetings
                          from {t('district_documents')} x
                          join {t('agency_meetings')} m on m.id=x.meeting_id
                          left join {t('agency_document_versions')} v on v.meeting_id=m.id
                         where x.district_id=%s""",
                    (did,),
                )
                corpus = dict(cur.fetchone())
                cur.execute(
                    f"""select topic,count(*)::int as count from {t('alerts')}
                         where district_id=%s and status='published' group by topic""",
                    (did,),
                )
                counts = {row["topic"]: row["count"] for row in cur.fetchall()}
                summary = dict(content.get("summary") or {})
                summary["documentsRead"] = corpus["documents"]
                summary["alertsPublished"] = sum(counts.values())
                if "meetingsCovered" in summary:
                    summary["meetingsCovered"] = corpus["meetings"]
                content["summary"] = summary
                categories = {row.get("id"): row for row in content.get("categories", [])}
                content["categories"] = []
                for topic, label in ALERT_TOPIC_LABELS.items():
                    category = categories.get(topic, {"id": topic, "label": label})
                    category["label"] = label
                    category["count"] = counts.get(topic, 0)
                    content["categories"].append(category)
                cur.execute(
                    f"""update {t('district_pages')} set content=%s::jsonb,updated_at=now()
                          where district_id=%s and page_kind='feed'""",
                    (json.dumps(content), did),
                )

            cur.execute(
                f"""update {t('agency_scrape_runs')}
                       set alert_review_status='notify_pending',alert_review_error=null,
                           alerts_published=%s,alert_review_alert_ids=%s,
                           alert_review_usage=%s::jsonb
                     where id=%s and district_id=%s and alert_review_status='reviewing'
                     returning id""",
                (len(stored), list(dict.fromkeys(newly_published_ids)),
                 json.dumps(usage), run_id, did),
            )
            if not cur.fetchone():
                raise ValueError(f"scrape run {run_id} is not a claimed review for {slug}")
        return {"stored": stored,
                "newAlertIds": list(dict.fromkeys(newly_published_ids))}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    district = sub.add_parser("put-district")
    district.add_argument("file", type=Path)
    page = sub.add_parser("put-page")
    page.add_argument("--district", required=True)
    page.add_argument("--kind", choices=sorted(PAGE_KINDS), required=True)
    page.add_argument("--status", choices=("draft", "published"), default="draft")
    page.add_argument("file", type=Path)
    alerts = sub.add_parser("put-alerts")
    alerts.add_argument("--district", required=True)
    alerts.add_argument("file", type=Path)
    category_page = sub.add_parser("put-category-page")
    category_page.add_argument("--district", required=True)
    category_page.add_argument("--topic", choices=sorted(ALERT_TOPICS), required=True)
    category_page.add_argument("--status", choices=("draft", "published"), default="draft")
    category_page.add_argument("--alert-count", type=int, required=True)
    category_page.add_argument("--source-alert-ids", default="",
                                help="comma-separated alert row ids")
    category_page.add_argument("file", type=Path)
    source = sub.add_parser("get-source")
    source.add_argument("--district", required=True)
    source.add_argument("--url", required=True)
    source_list = sub.add_parser("list-sources")
    source_list.add_argument("--district", required=True)
    source_list.add_argument("--limit", type=int, default=5_000)
    reconcile = sub.add_parser("reconcile-drive-sources")
    reconcile.add_argument("--district", required=True)
    reconcile.add_argument("--apply", action="store_true")
    reconcile.add_argument("--confirm")
    reconcile.add_argument("file", type=Path)
    detach = sub.add_parser("detach-sources")
    detach.add_argument("--district", required=True)
    detach.add_argument("--apply", action="store_true")
    detach.add_argument("--confirm")
    detach.add_argument("file", type=Path)
    migrate = sub.add_parser("migrate-source-identities")
    migrate.add_argument("--district", required=True)
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--confirm")
    migrate.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "put-district":
            value = put_district(read_json(args.file))
        elif args.command == "put-page":
            value = put_page(args.district, args.kind, read_json(args.file), args.status)
        elif args.command == "put-alerts":
            value = put_alerts(args.district, read_json(args.file))
        elif args.command == "put-category-page":
            ids = [int(x) for x in args.source_alert_ids.split(",") if x.strip()]
            value = put_category_page(args.district, args.topic, read_json(args.file),
                                       args.status, args.alert_count, ids)
        elif args.command == "get-source":
            with connect() as conn:
                did = district_id(conn, args.district)
                with dict_cursor(conn) as cur:
                    cur.execute(
                        f"""select m.source_url,m.storage_key,m.sha256,m.content_type,m.bytes
                            from {t('agency_meetings')} m
                            join {t('district_documents')} x on x.meeting_id=m.id
                            where x.district_id=%s and m.source_url=%s""",
                        (did, args.url),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError("source URL is not collected for this district")
                    value = dict(row)
        elif args.command == "reconcile-drive-sources":
            value = reconcile_drive_sources(
                args.district, read_json(args.file), apply=args.apply, confirm=args.confirm
            )
        elif args.command == "detach-sources":
            value = detach_sources(
                args.district, read_json(args.file), apply=args.apply, confirm=args.confirm
            )
        elif args.command == "migrate-source-identities":
            value = migrate_source_identities(
                args.district, read_json(args.file), apply=args.apply, confirm=args.confirm
            )
        else:
            value = list_sources(args.district, args.limit)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, default=str))
        sys.exit(1)
    print(json.dumps({"ok": True, "result": value}, default=str))


if __name__ == "__main__":
    main()
