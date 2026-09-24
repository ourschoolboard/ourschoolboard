#!/usr/bin/env python3
"""Register a validated scraper and its recurring schedule without arbitrary SQL.

    python3 scripts/tools/scraper_store.py register --district weston-ma \
      --script scrapers/weston.py --config weston-sources.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, t  # noqa: E402
from schedule import next_run_after, validate_schedule  # noqa: E402

MAX_DOCUMENT_BYTES = 250 * 1024 * 1024
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 30 * 60
MAX_STALE_SECONDS = 24 * 60 * 60
MAX_BROWSER_FETCHES = 50
MAX_BROWSER_NETWORK_REQUESTS = 1000
MAX_BROWSER_TIMEOUT_SECONDS = 60
MAX_BROWSER_RESPONSE_BYTES = 20 * 1024 * 1024
BROWSER_PROVIDERS = {"direct", "isp_proxy", "scraping_browser"}
DEFAULT_CADENCE_DAYS = 14
DRIVE_RESOLVER_TERMS_HOSTS = ("drive.google.com", "googleusercontent.com")
DRIVE_VIDEO_TERMS_HOSTS = ("drive.google.com", "drive.usercontent.google.com")
ALLOWED_RESOLVER_TERMS_STATUSES = frozenset({"none_found", "permissive"})
DEFAULT_VIDEO_MODEL = "qwen/qwen3-asr-1.7b"
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+$")


def validate_config(config: dict) -> None:
    if "stable_pdf_fingerprint" in config:
        raise ValueError(
            "config.stable_pdf_fingerprint was removed; documents use URL/filename identity"
        )
    drive_resolver = config.get("google_drive_view_pdf")
    if drive_resolver is not None and not isinstance(drive_resolver, bool):
        raise ValueError("config.google_drive_view_pdf must be a boolean")
    drive_media_resolver = config.get("google_drive_view_media")
    if drive_media_resolver is not None and not isinstance(drive_media_resolver, bool):
        raise ValueError("config.google_drive_view_media must be a boolean")
    youtube = config.get("youtube_transcripts")
    if youtube is not None:
        if not isinstance(youtube, dict):
            raise ValueError("config.youtube_transcripts must be an object")
        unknown = sorted(set(youtube) - {"enabled", "languages"})
        if unknown:
            raise ValueError(f"unknown config.youtube_transcripts option: {unknown[0]}")
        if not isinstance(youtube.get("enabled"), bool):
            raise ValueError("config.youtube_transcripts.enabled must be a boolean")
        languages = youtube.get("languages", ["en"])
        if (not isinstance(languages, list) or not 1 <= len(languages) <= 5
                or any(not isinstance(item, str)
                       or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", item)
                       for item in languages)):
            raise ValueError(
                "config.youtube_transcripts.languages must contain 1-5 language codes"
            )
    granicus = config.get("granicus_captions")
    if granicus is not None:
        if not isinstance(granicus, dict):
            raise ValueError("config.granicus_captions must be an object")
        unknown = sorted(set(granicus) - {"enabled"})
        if unknown:
            raise ValueError(f"unknown config.granicus_captions option: {unknown[0]}")
        if not isinstance(granicus.get("enabled"), bool):
            raise ValueError("config.granicus_captions.enabled must be a boolean")
    video = config.get("video_transcripts")
    if video is not None:
        if not isinstance(video, dict):
            raise ValueError("config.video_transcripts must be an object")
        unknown = sorted(set(video) - {
            "enabled", "model", "language", "chunk_seconds", "concurrency",
            "google_drive",
        })
        if unknown:
            raise ValueError(f"unknown config.video_transcripts option: {unknown[0]}")
        if not isinstance(video.get("enabled"), bool):
            raise ValueError("config.video_transcripts.enabled must be a boolean")
        model = video.get("model", DEFAULT_VIDEO_MODEL)
        if not isinstance(model, str) or len(model) > 120 or not MODEL_RE.fullmatch(model):
            raise ValueError("config.video_transcripts.model must be a provider/model slug")
        language = video.get("language")
        if language is not None and (
            not isinstance(language, str)
            or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?", language)
        ):
            raise ValueError("config.video_transcripts.language must be a language code")
        chunk_seconds = video.get("chunk_seconds", 300)
        if (isinstance(chunk_seconds, bool) or not isinstance(chunk_seconds, int)
                or not 30 <= chunk_seconds <= 600):
            raise ValueError("config.video_transcripts.chunk_seconds must be 30-600")
        concurrency = video.get("concurrency", 3)
        if (isinstance(concurrency, bool) or not isinstance(concurrency, int)
                or not 1 <= concurrency <= 6):
            raise ValueError("config.video_transcripts.concurrency must be 1-6")
        google_drive = video.get("google_drive", False)
        if not isinstance(google_drive, bool):
            raise ValueError("config.video_transcripts.google_drive must be a boolean")
    bounds = {
        "document_max_bytes": (1, MAX_DOCUMENT_BYTES),
        "video_max_bytes": (1, MAX_VIDEO_BYTES),
        "timeout_seconds": (30, MAX_TIMEOUT_SECONDS),
        "stale_run_seconds": (15 * 60, MAX_STALE_SECONDS),
    }
    for name, (minimum, maximum) in bounds.items():
        value = config.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"config.{name} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(
                f"config.{name} must be between {minimum} and {maximum}"
            )

    browser = config.get("browser")
    if browser is None:
        return
    if not isinstance(browser, dict):
        raise ValueError("config.browser must be an object")
    allowed = {
        "enabled", "provider", "max_fetches", "max_network_requests",
        "timeout_seconds", "max_response_bytes",
    }
    unknown = sorted(set(browser) - allowed)
    if unknown:
        raise ValueError(f"unknown config.browser option: {unknown[0]}")
    if not isinstance(browser.get("enabled"), bool):
        raise ValueError("config.browser.enabled must be a boolean")
    provider = browser.get("provider", "isp_proxy")
    if provider not in BROWSER_PROVIDERS:
        raise ValueError(
            f"config.browser.provider must be one of {sorted(BROWSER_PROVIDERS)}"
        )
    browser_bounds = {
        "max_fetches": (1, MAX_BROWSER_FETCHES),
        "max_network_requests": (1, MAX_BROWSER_NETWORK_REQUESTS),
        "timeout_seconds": (1, MAX_BROWSER_TIMEOUT_SECONDS),
        "max_response_bytes": (1, MAX_BROWSER_RESPONSE_BYTES),
    }
    for name, (minimum, maximum) in browser_bounds.items():
        value = browser.get(name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"config.browser.{name} must be an integer")
        if not minimum <= value <= maximum:
            raise ValueError(
                f"config.browser.{name} must be between {minimum} and {maximum}"
            )


def validate_drive_resolver_terms(statuses: dict[str, str | None]) -> None:
    for host in DRIVE_RESOLVER_TERMS_HOSTS:
        status = statuses.get(host)
        if status not in ALLOWED_RESOLVER_TERMS_STATUSES:
            raise ValueError(
                "config.google_drive_view_pdf requires a recorded non-prohibiting "
                f"terms check for {host}"
            )


def validate_drive_video_terms(statuses: dict[str, str | None]) -> None:
    for host in DRIVE_VIDEO_TERMS_HOSTS:
        if statuses.get(host) not in ALLOWED_RESOLVER_TERMS_STATUSES:
            raise ValueError(
                "config.video_transcripts.google_drive requires a recorded "
                f"non-prohibiting terms check for {host}"
            )


def validate_drive_media_terms(statuses: dict[str, str | None]) -> None:
    for host in DRIVE_VIDEO_TERMS_HOSTS:
        if statuses.get(host) not in ALLOWED_RESOLVER_TERMS_STATUSES:
            raise ValueError(
                "config.google_drive_view_media requires a recorded "
                f"non-prohibiting terms check for {host}"
            )


def validate_source(source: str, config: dict | None = None) -> None:
    if len(source.encode()) > 200_000:
        raise ValueError("scraper source exceeds 200KB")
    tree = ast.parse(source)
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    scrape = next((node for node in functions if node.name == "scrape"), None)
    if not scrape or len(scrape.args.args) != 1:
        raise ValueError("scraper must define scrape(ctx) with exactly one argument")
    forbidden = {
        "psycopg2", "boto3", "botocore", "subprocess", "socket",
        # Browser access must go through ctx.browser_get so the runner owns its
        # limits and teardown. Scraper code never launches a browser directly.
        "playwright", "selenium",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names = {(node.module or "").split(".")[0]}
        else:
            continue
        blocked = names & forbidden
        if blocked:
            raise ValueError(f"scraper imports forbidden capability: {sorted(blocked)[0]}")
    uses_browser = any(
        isinstance(node, ast.Attribute) and node.attr == "browser_get"
        for node in ast.walk(tree)
    )
    if uses_browser and not (
        isinstance(config, dict)
        and isinstance(config.get("browser"), dict)
        and config["browser"].get("enabled") is True
    ):
        raise ValueError(
            "scraper uses ctx.browser_get but config.browser.enabled is not true"
        )


def next_scraper_version(cur, agency_id: int) -> int:
    """Allocate within the agency-wide uniqueness constraint.

    A governing agency can own multiple district or school entities. The
    database key is (agency_id, version), so counting versions only within one
    district can collide as soon as a sibling entity registers its scraper.
    """
    cur.execute(
        f"select coalesce(max(version),0)+1 as version from {t('agency_scrapers')} where agency_id=%s",
        (agency_id,),
    )
    return cur.fetchone()["version"]


def register(slug: str, script: Path, config: dict, *, notes: str | None,
             model: str | None, cadence_days: int, enabled: bool,
             schedule: dict | None = None) -> dict:
    source = script.read_text(encoding="utf-8")
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    validate_config(config)
    validate_source(source, config)
    pages = config.get("source_pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError("config.source_pages must be a non-empty array")
    if not 1 <= cadence_days <= 31:
        raise ValueError("cadence days must be between 1 and 31")
    schedule = validate_schedule(schedule) if schedule is not None else None
    next_run_at = next_run_after(schedule) if schedule is not None else None
    if schedule is not None and enabled and next_run_at is None:
        raise ValueError("schedule has no future agenda or follow-up run")

    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select d.id,d.agency_id,a.status as agency_status,a.terms_status,
                           exists(select 1 from {t('agency_tos_checks')} c
                                  where c.agency_id=a.id and c.source='terms') as has_compliance,
                           (select p.status from {t('platform_terms')} p
                             where p.host='drive.google.com'
                             order by p.checked_at desc, p.id desc limit 1) as drive_terms_status,
                           (select p.status from {t('platform_terms')} p
                             where p.host='googleusercontent.com'
                             order by p.checked_at desc, p.id desc limit 1) as usercontent_terms_status,
                           (select p.status from {t('platform_terms')} p
                             where p.host='drive.usercontent.google.com'
                             order by p.checked_at desc, p.id desc limit 1) as drive_media_terms_status,
                           (select p.status from {t('platform_terms')} p
                             where p.host='www.youtube.com'
                             order by p.checked_at desc, p.id desc limit 1) as youtube_terms_status
                    from {t('districts')} d
                    left join {t('agencies')} a on a.id=d.agency_id
                    where d.slug=%s""",
                (slug,),
            )
            district = cur.fetchone()
            if not district:
                raise ValueError(f"unknown district: {slug}")
            if not district["agency_id"]:
                raise ValueError("district has no governing agency; set agencyLeaid with put-district")
            if district["agency_status"] == "tos_blocked" or district["terms_status"] == "prohibits":
                raise ValueError("governing agency is blocked by recorded terms; scraper not registered")
            if not district["has_compliance"]:
                raise ValueError("no recorded terms check for governing agency; run compliance.py first")
            if config.get("google_drive_view_pdf"):
                validate_drive_resolver_terms({
                    "drive.google.com": district["drive_terms_status"],
                    "googleusercontent.com": district["usercontent_terms_status"],
                })
            if config.get("google_drive_view_media"):
                validate_drive_media_terms({
                    "drive.google.com": district["drive_terms_status"],
                    "drive.usercontent.google.com": district["drive_media_terms_status"],
                })
            if (config.get("youtube_transcripts") or {}).get("enabled"):
                if district["youtube_terms_status"] not in ALLOWED_RESOLVER_TERMS_STATUSES:
                    raise ValueError(
                        "config.youtube_transcripts requires a recorded non-prohibiting "
                        "terms check for www.youtube.com"
                    )
            if (config.get("video_transcripts") or {}).get("google_drive"):
                validate_drive_video_terms({
                    "drive.google.com": district["drive_terms_status"],
                    "drive.usercontent.google.com": district["drive_media_terms_status"],
                })

            version = next_scraper_version(cur, district["agency_id"])
            cur.execute(
                f"update {t('agency_scrapers')} set is_active=false where district_id=%s and is_active",
                (district["id"],),
            )
            cur.execute(
                f"""insert into {t('agency_scrapers')}
                      (agency_id,district_id,version,source,language,origin,model,notes,
                       is_active,kind,config,lookback_days)
                    values (%s,%s,%s,%s,'python','authored',%s,%s,true,'generated',%s::jsonb,365)
                    returning id,version""",
                (district["agency_id"], district["id"], version, source, model, notes,
                 json.dumps(config)),
            )
            scraper = cur.fetchone()
            cur.execute(
                f"""insert into {t('scrape_schedules')}
                      (district_id,scraper_id,cadence_days,enabled,next_run_at,
                       schedule_config,updated_at)
                    values (%s,%s,%s,%s,
                            case when %s::jsonb is null
                              then now()+(%s || ' days')::interval else %s end,
                            %s::jsonb,now())
                    on conflict (district_id) do update set
                      scraper_id=excluded.scraper_id, cadence_days=excluded.cadence_days,
                      enabled=excluded.enabled, next_run_at=excluded.next_run_at,
                      schedule_config=excluded.schedule_config,
                      updated_at=now()
                    returning id,enabled,next_run_at,schedule_config""",
                (district["id"], scraper["id"], cadence_days, enabled,
                 json.dumps(schedule) if schedule is not None else None,
                 cadence_days, next_run_at,
                 json.dumps(schedule) if schedule is not None else None),
            )
            schedule = cur.fetchone()
    return {"district": slug, "scraperId": scraper["id"], "version": version,
            "schedule": dict(schedule)}


def status(slug: str) -> dict:
    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select d.slug,q.enabled,q.cadence_days,q.next_run_at,q.schedule_config,
                           q.last_started_at,q.last_completed_at,s.id as scraper_id,s.version
                    from {t('districts')} d
                    left join {t('scrape_schedules')} q on q.district_id=d.id
                    left join {t('agency_scrapers')} s on s.id=q.scraper_id
                    where d.slug=%s""", (slug,),
            )
            schedule = cur.fetchone()
            if not schedule:
                raise ValueError(f"unknown district: {slug}")
            cur.execute(
                f"""select r.id,r.trigger,r.status,r.documents_seen,r.documents_new,
                           r.failure_kind,r.error,r.started_at,r.finished_at,r.detail
                    from {t('agency_scrape_runs')} r
                    join {t('districts')} d on d.id=r.district_id
                    where d.slug=%s order by r.id desc limit 10""", (slug,),
            )
            return {"schedule": dict(schedule), "recentRuns": [dict(row) for row in cur.fetchall()]}


def get_active(slug: str) -> dict:
    """Return the active authored scraper so a failed collector can be repaired."""
    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select s.id,s.version,s.source,s.config,s.notes,s.model,s.lookback_days
                    from {t('districts')} d
                    join {t('scrape_schedules')} q on q.district_id=d.id
                    join {t('agency_scrapers')} s on s.id=q.scraper_id
                    where d.slug=%s and s.is_active
                    order by s.version desc limit 1""",
                (slug,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"district has no active scraper: {slug}")
            return dict(row)


def pause_all() -> dict:
    """Disable every recurring scrape schedule in the selected environment."""
    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""update {t('scrape_schedules')}
                       set enabled=false,updated_at=now()
                     where enabled
                     returning district_id"""
            )
            paused = [row["district_id"] for row in cur.fetchall()]
    return {"pausedSchedules": len(paused), "districtIds": paused}


def pause(slug: str) -> dict:
    """Disable one recurring schedule without changing its scraper definition."""
    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""update {t('scrape_schedules')} q
                       set enabled=false,updated_at=now()
                      from {t('districts')} d
                     where q.district_id=d.id and d.slug=%s
                     returning q.id,q.enabled""",
                (slug,),
            )
            schedule = cur.fetchone()
            if not schedule:
                raise ValueError(f"district has no scrape schedule: {slug}")
    return {"district": slug, "schedule": dict(schedule)}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register")
    reg.add_argument("--district", required=True)
    reg.add_argument("--script", type=Path, required=True)
    reg.add_argument("--config", type=Path, required=True)
    reg.add_argument("--notes")
    reg.add_argument("--model")
    reg.add_argument("--cadence-days", type=int, default=DEFAULT_CADENCE_DAYS)
    reg.add_argument(
        "--schedule", type=Path,
        help="board-calendar schedule JSON; replaces interval cadence timing",
    )
    reg.add_argument("--disabled", action="store_true")
    show = sub.add_parser("status")
    show.add_argument("--district", required=True)
    active = sub.add_parser("get-active")
    active.add_argument("--district", required=True)
    pause_one = sub.add_parser("pause")
    pause_one.add_argument("--district", required=True)
    pause_one.add_argument("--confirm", required=True)
    pause_every = sub.add_parser("pause-all")
    pause_every.add_argument("--confirm", required=True)
    args = parser.parse_args()
    try:
        if args.command == "register":
            result = register(args.district, args.script,
                              json.loads(args.config.read_text(encoding="utf-8")),
                              notes=args.notes, model=args.model,
                              cadence_days=args.cadence_days, enabled=not args.disabled,
                              schedule=(json.loads(args.schedule.read_text(encoding="utf-8"))
                                        if args.schedule else None))
        elif args.command == "status":
            result = status(args.district)
        elif args.command == "pause":
            if args.confirm != f"PAUSE {args.district}":
                raise ValueError(
                    f'pause requires --confirm "PAUSE {args.district}"'
                )
            result = pause(args.district)
        elif args.command == "pause-all":
            if args.confirm != "PAUSE_ALL_SCRAPERS":
                raise ValueError("pause-all requires --confirm PAUSE_ALL_SCRAPERS")
            result = pause_all()
        else:
            result = {"scraper": get_active(args.district)}
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    print(json.dumps({"ok": True, **result}, default=str))


if __name__ == "__main__":
    main()
