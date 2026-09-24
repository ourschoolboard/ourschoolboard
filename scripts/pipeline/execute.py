#!/usr/bin/env python3
"""Run registered scrapers and persist their results through trusted parents.

Generated code runs in sandbox.py without database or Spaces credentials. This
parent owns those capabilities: it validates candidates, downloads the bytes,
uploads immutable versions, and writes a scrape-run result even on failure.

    python3 scripts/pipeline/execute.py --district weston-ma --trigger manual
    python3 scripts/pipeline/execute.py --due
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from email.message import Message
import json
import os
import re
import signal
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "scripts" / "tools"))
from db import connect, dict_cursor, t  # noqa: E402
from schedule import next_run_after  # noqa: E402
from sandbox import run_scraper  # noqa: E402
from spaces_store import put_file  # noqa: E402
from safe_http import UnsafeUrlError, safe_get  # noqa: E402
from youtube_transcript import (  # noqa: E402
    YouTubeTranscriptError,
    fetch_to_file as fetch_youtube_transcript,
    video_id_from_url,
)
from video_transcript import fetch_to_file as fetch_video_transcript  # noqa: E402

USER_AGENT = os.environ.get(
    "SCRAPER_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
)
MAX_DOCUMENT_BYTES = int(os.environ.get("SCRAPER_DOCUMENT_MAX_BYTES", str(50 * 1024 * 1024)))
MAX_CONFIGURED_DOCUMENT_BYTES = 250 * 1024 * 1024
DEFAULT_VIDEO_MAX_BYTES = 512 * 1024 * 1024
MAX_CONFIGURED_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_SCRAPER_TIMEOUT = int(os.environ.get("SCRAPER_TIMEOUT", "300"))
MAX_CONFIGURED_SCRAPER_TIMEOUT = 30 * 60
DEFAULT_STALE_RUN_SECONDS = int(os.environ.get("SCRAPER_STALE_RUN_SECONDS", str(2 * 60 * 60)))
MIN_STALE_RUN_SECONDS = 15 * 60
MAX_STALE_RUN_SECONDS = 24 * 60 * 60
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
DRIVE_VIEW_RE = re.compile(r"^/file/d/[A-Za-z0-9_-]+/view$")
DRIVE_PDF_HOST_RE = re.compile(
    r"^doc-[0-9a-z]+-a0-apps-viewer\.googleusercontent\.com$"
)
DRIVE_MANIFEST_URL_RE = re.compile(
    rb'"(https://drive\.google\.com/viewer/upload\?ds\\u003d[^"\\]+(?:\\u[0-9a-fA-F]{4}|\\.|[^"\\])*)"'
)
DRIVE_MEDIA_URL_RE = re.compile(
    rb'"(https://drive\.usercontent\.google\.com/uc\?id\\u003d'
    rb'[A-Za-z0-9_-]+\\u0026export\\u003ddownload)"'
)
GRANICUS_CLIP_RE = re.compile(r"^/player/clip/(\d+)$")
MAX_DRIVE_INTERMEDIATE_BYTES = 2 * 1024 * 1024
MAX_ROBOTS_BYTES = 1024 * 1024
_ROBOTS_CACHE: dict[str, tuple[RobotFileParser, float]] = {}
_ORIGIN_LAST_REQUEST: dict[str, float] = {}
DOCUMENT_MIN_INTERVAL = max(
    0.0, float(os.environ.get("SCRAPER_DOWNLOAD_MIN_INTERVAL", "0.75"))
)


class HostDownloadThrottle:
    """Keep trusted-parent document fetches from bursting against one host."""

    def __init__(self, min_interval: float, *, clock=time.monotonic, sleep=time.sleep):
        self.min_interval = max(0.0, min_interval)
        self._clock = clock
        self._sleep = sleep
        self._last_finished: dict[str, float] = {}

    @staticmethod
    def host(url: str) -> str:
        return (urlsplit(url).hostname or "").lower()

    def wait(self, url: str) -> None:
        host = self.host(url)
        if not host or host not in self._last_finished:
            return
        remaining = self.min_interval - (self._clock() - self._last_finished[host])
        if remaining > 0:
            self._sleep(remaining)

    def finished(self, url: str) -> None:
        host = self.host(url)
        if host:
            self._last_finished[host] = self._clock()


DOCUMENT_DOWNLOAD_THROTTLE = HostDownloadThrottle(DOCUMENT_MIN_INTERVAL)


class RunAlreadyActive(RuntimeError):
    pass


class TerminationRequested(RuntimeError):
    pass


class DriveViewResolverError(requests.RequestException):
    pass


class GranicusCaptionError(requests.RequestException):
    pass


def is_granicus_clip_url(url: str) -> bool:
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and (host == "granicus.com" or host.endswith(".granicus.com"))
        and GRANICUS_CLIP_RE.fullmatch(parts.path) is not None
        and not parts.username and not parts.password
    )


def fetch_granicus_captions(url: str, destination: Path, *, max_bytes: int) -> str:
    if not is_granicus_clip_url(url):
        raise GranicusCaptionError("Granicus caption resolver requires a canonical clip URL")
    with safe_get(HTTP, url, timeout=(15, 90)) as viewer:
        viewer.raise_for_status()
        if len(viewer.content) > 2 * 1024 * 1024:
            raise GranicusCaptionError("Granicus viewer response is too large")
        track = BeautifulSoup(viewer.content, "html.parser").select_one(
            "track[kind='captions'][src]"
        )
        if not track:
            raise GranicusCaptionError("Granicus viewer has no caption track")
        caption_url = urljoin(viewer.url, track["src"])
    with safe_get(HTTP, caption_url, timeout=(15, 90)) as captions:
        captions.raise_for_status()
        body = captions.content
    if len(body) > max_bytes:
        raise GranicusCaptionError("Granicus caption track is too large")
    text = body.decode("utf-8-sig", errors="replace").strip()
    if not text.startswith("WEBVTT"):
        raise GranicusCaptionError("Granicus caption track is not WebVTT")
    has_cues = bool(re.search(r"\d{2}:\d{2}(?::\d{2})?\.\d{3}\s+-->", text))
    status = "available" if has_cues else "empty; speech-to-text backfill required"
    rendered = (
        "Board meeting video transcript\n"
        f"Source recording: {url}\n"
        "Transcript type: district-published Granicus WebVTT captions; verify against the recording.\n"
        f"Caption track status: {status}\n\n{text}\n"
    ).encode("utf-8")
    if len(rendered) > max_bytes:
        raise GranicusCaptionError("Rendered Granicus transcript is too large")
    destination.write_bytes(rendered)
    return "text/plain"


def _bounded_config_int(target: dict, name: str, default: int, minimum: int,
                        maximum: int) -> int:
    configured = (target.get("config") or {}).get(name)
    if configured is None:
        return default
    if isinstance(configured, bool):
        raise ValueError(f"config.{name} must be an integer")
    try:
        value = int(configured)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"config.{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"config.{name} must be between {minimum} and {maximum}")
    return value


def scraper_timeout(target: dict) -> int:
    return _bounded_config_int(
        target, "timeout_seconds", DEFAULT_SCRAPER_TIMEOUT, 30,
        MAX_CONFIGURED_SCRAPER_TIMEOUT,
    )


def stale_run_seconds(target: dict) -> int:
    requested = _bounded_config_int(
        target, "stale_run_seconds", DEFAULT_STALE_RUN_SECONDS,
        MIN_STALE_RUN_SECONDS, MAX_STALE_RUN_SECONDS,
    )
    return max(requested, scraper_timeout(target) * 2)


def http_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, connect=3, read=3, status=3, backoff_factor=0.5,
        status_forcelist=RETRYABLE_STATUS,
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )))
    return session


HTTP = http_session()


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _strict_https_url(url: str, *, host: str | None = None) -> tuple[str, str]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise DriveViewResolverError("Drive resolver received an invalid URL") from exc
    hostname = (parts.hostname or "").lower()
    if (parts.scheme != "https" or not hostname or parts.username or parts.password
            or port not in (None, 443) or (host is not None and hostname != host)):
        raise DriveViewResolverError("Drive resolver rejected a non-allowlisted URL")
    return hostname, parts.path


def is_google_drive_view_url(url: str) -> bool:
    try:
        host, path = _strict_https_url(url, host="drive.google.com")
    except DriveViewResolverError:
        return False
    return host == "drive.google.com" and bool(DRIVE_VIEW_RE.fullmatch(path))


def _bounded_response(response: requests.Response, url: str, max_bytes: int) -> bytes:
    if not 200 <= response.status_code < 300:
        raise DriveViewResolverError(
            f"Drive resolver received HTTP {response.status_code}: {url}"
        )
    response.raise_for_status()
    chunks = []
    total = 0
    for chunk in response.iter_content(1 << 16):
        total += len(chunk)
        if total > max_bytes:
            raise DriveViewResolverError(
                f"Drive resolver response exceeded {max_bytes} bytes: {url}"
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    response._content = data
    return data


def _robots_cache_key(url: str) -> str:
    """Cache/rate-limit key for robots-policy lookups and crawl-delay pacing.

    Google's Drive PDF-export host is a fixed URL pattern with a randomized
    subdomain on every single request (see DRIVE_PDF_HOST_RE) -- the same
    underlying service, never actually a distinct origin to be polite to
    separately. Keying the cache and the crawl-delay clock by the literal
    hostname forced a full robots.txt round trip for every document (a "new"
    host every time), landing right in the middle of the
    viewer -> manifest -> pdf sequence -- exactly where Google's short-lived
    signed PDF URL is most likely to expire before the final fetch happens.
    Manually replaying the same three-hop fetch without that per-document
    robots.txt round trip completed in well under 2 seconds; the pipeline's
    version was failing at this exact step.

    Normalizing the key for this one known, allowlisted pattern fixes it:
    only the first PDF host seen in a run pays the robots.txt cost.
    RobotFileParser.can_fetch() only inspects the URL path, not the host it
    was parsed from, so reusing a cached parser across different literal
    hostnames on the same pattern is correct, not just faster.
    """
    host = urlsplit(url).netloc.lower()
    if DRIVE_PDF_HOST_RE.fullmatch(host):
        return "pattern:drive-pdf-export-host"
    return _origin(url)


def _robots_policy(url: str) -> tuple[RobotFileParser, float]:
    key = _robots_cache_key(url)
    cached = _ROBOTS_CACHE.get(key)
    if cached is not None:
        return cached
    origin = _origin(url)
    robots_url = f"{origin}/robots.txt"
    try:
        response = safe_get(
            HTTP, robots_url, timeout=(15, 30), stream=True, max_redirects=0
        )
        _ORIGIN_LAST_REQUEST[key] = time.monotonic()
        try:
            if response.status_code == 404:
                lines: list[str] = []
            else:
                raw = _bounded_response(response, robots_url, MAX_ROBOTS_BYTES)
                lines = raw.decode("utf-8", errors="replace").splitlines()
        finally:
            response.close()
    except requests.RequestException as exc:
        raise DriveViewResolverError(
            f"could not verify robots policy for {origin}"
        ) from exc
    parser = RobotFileParser(robots_url)
    parser.parse(lines)
    if not parser.can_fetch(USER_AGENT, url):
        raise DriveViewResolverError(f"robots policy disallows Drive resolver URL: {url}")
    delay = parser.crawl_delay(USER_AGENT)
    if delay is None:
        delay = parser.crawl_delay("*")
    policy = (parser, max(1.0, float(delay or 0)))
    _ROBOTS_CACHE[key] = policy
    return policy


def _resolver_get(url: str, *, max_bytes: int) -> requests.Response:
    parser, delay = _robots_policy(url)
    if not parser.can_fetch(USER_AGENT, url):
        raise DriveViewResolverError(f"robots policy disallows Drive resolver URL: {url}")
    key = _robots_cache_key(url)
    wait = delay - (time.monotonic() - _ORIGIN_LAST_REQUEST.get(key, 0.0))
    if wait > 0:
        time.sleep(wait)
    try:
        response = safe_get(
            HTTP, url, timeout=(15, 90), stream=True, max_redirects=0
        )
        _ORIGIN_LAST_REQUEST[key] = time.monotonic()
        try:
            _bounded_response(response, url, max_bytes)
        finally:
            response.close()
    except requests.RequestException as exc:
        raise DriveViewResolverError(f"Drive resolver fetch failed: {url}") from exc
    final_host, _ = _strict_https_url(response.url)
    requested_host, _ = _strict_https_url(url)
    if final_host != requested_host:
        raise DriveViewResolverError("Drive resolver rejected a cross-host redirect")
    return response


def _drive_manifest_url(html: bytes) -> str:
    match = DRIVE_MANIFEST_URL_RE.search(html)
    if not match:
        raise DriveViewResolverError("Drive viewer did not expose a PDF manifest")
    try:
        url = json.loads(b'"' + match.group(1) + b'"')
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveViewResolverError("Drive viewer manifest URL was malformed") from exc
    host, path = _strict_https_url(url, host="drive.google.com")
    if host != "drive.google.com" or path != "/viewer/upload":
        raise DriveViewResolverError("Drive viewer manifest URL was not allowlisted")
    return url


def _drive_pdf_url(payload: bytes) -> str:
    if not payload.startswith(b")]}'\n"):
        raise DriveViewResolverError("Drive viewer manifest lacked its anti-XSSI prefix")
    try:
        manifest = json.loads(payload.split(b"\n", 1)[1])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveViewResolverError("Drive viewer manifest was malformed") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("status"), str):
        raise DriveViewResolverError("Drive viewer manifest lacked status metadata")
    url = manifest.get("pdf")
    if not isinstance(url, str):
        raise DriveViewResolverError("Drive viewer manifest lacked a PDF URL")
    host, path = _strict_https_url(url)
    if not DRIVE_PDF_HOST_RE.fullmatch(host) or not path.startswith("/viewer/secure/pdf/"):
        raise DriveViewResolverError("Drive viewer PDF URL was not allowlisted")
    return url


def _drive_file_id(view_url: str) -> str:
    """Return the opaque ID only after validating a canonical public view URL."""
    parts = urlsplit(view_url)
    match = DRIVE_VIEW_RE.fullmatch(parts.path)
    if (parts.scheme != "https" or (parts.hostname or "").lower() != "drive.google.com"
            or match is None or parts.query or parts.fragment):
        raise DriveViewResolverError("Drive resolver requires a canonical public view URL")
    return parts.path.split("/")[3]


def _drive_confirmation_url(response: requests.Response, *, media_id: str,
                            source_url: str) -> str | None:
    """Validate Drive's large-file form and return its single trusted follow-up URL."""
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    if content_type != "text/html":
        return None
    confirmation = _bounded_response(response, source_url, MAX_DRIVE_INTERMEDIATE_BYTES)
    form = BeautifulSoup(confirmation, "html.parser").select_one(
        "form#download-form[action='https://drive.usercontent.google.com/download']"
    )
    if form is None:
        raise DriveViewResolverError("Drive download confirmation form was missing")
    fields = {
        node.get("name"): node.get("value")
        for node in form.select("input[type='hidden'][name][value]")
    }
    allowed = {"id", "export", "confirm", "uuid"}
    if (set(fields) - allowed or fields.get("id") != media_id
            or fields.get("export") != "download" or fields.get("confirm") != "t"):
        raise DriveViewResolverError("Drive download confirmation fields were invalid")
    return f"https://drive.usercontent.google.com/download?{urlencode(fields)}"


def _resolve_google_drive_download(view_url: str, destination: Path, *,
                                   max_bytes: int) -> str:
    """Use Drive's documented public download flow after a viewer export fails.

    The only generated request is pinned to the opaque ID in the canonical
    view URL.  Large files may return Drive's virus-scan form; that form is
    parsed with an allowlist before its confirmation URL is followed.
    """
    media_id = _drive_file_id(view_url)
    media_url = "https://drive.usercontent.google.com/download?" + urlencode({
        "id": media_id, "export": "download", "confirm": "t",
    })
    response = _drive_download_response(media_url)
    try:
        confirmation_url = _drive_confirmation_url(
            response, media_id=media_id, source_url=media_url,
        )
        if confirmation_url is not None:
            response.close()
            response = _drive_download_response(confirmation_url)
        content_type = _write_bounded_response(
            response, destination, max_bytes=max_bytes, url=view_url,
        )
        with destination.open("rb") as handle:
            signature = handle.read(5)
        # Drive's public download endpoint commonly labels a verified PDF as
        # application/octet-stream.  The byte signature, not that generic
        # transport label, is the authoritative PDF check here.
        if signature != b"%PDF-":
            raise DriveViewResolverError("Drive download fallback final response was not a PDF")
        return "application/pdf"
    finally:
        response.close()


def resolve_google_drive_view(url: str, destination: Path, *, max_bytes: int) -> str:
    """Resolve a public Drive viewer shell to its byte-exact official PDF."""
    if not is_google_drive_view_url(url):
        raise DriveViewResolverError("Drive resolver requires a canonical public view URL")
    try:
        viewer = _resolver_get(url, max_bytes=MAX_DRIVE_INTERMEDIATE_BYTES)
        viewer_type = viewer.headers.get("content-type", "").split(";", 1)[0].lower()
        if viewer_type != "text/html":
            raise DriveViewResolverError("Drive viewer response was not HTML")
        manifest_url = _drive_manifest_url(viewer.content)
        manifest = _resolver_get(manifest_url, max_bytes=MAX_DRIVE_INTERMEDIATE_BYTES)
        manifest_type = manifest.headers.get("content-type", "").split(";", 1)[0].lower()
        if manifest_type != "application/json":
            raise DriveViewResolverError("Drive viewer manifest was not JSON")
        pdf_url = _drive_pdf_url(manifest.content)
        pdf = _resolver_get(pdf_url, max_bytes=max_bytes)
        content_type = pdf.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "application/pdf" or not pdf.content.startswith(b"%PDF-"):
            raise DriveViewResolverError("Drive resolver final response was not a PDF")
        destination.write_bytes(pdf.content)
        return "application/pdf"
    except DriveViewResolverError as viewer_error:
        try:
            return _resolve_google_drive_download(url, destination, max_bytes=max_bytes)
        except DriveViewResolverError as download_error:
            raise DriveViewResolverError(
                f"Drive viewer export failed ({viewer_error}); "
                f"direct download fallback failed ({download_error})"
            ) from download_error


def _drive_media_url(view_url: str, html: bytes) -> str:
    match = DRIVE_MEDIA_URL_RE.search(html)
    if not match:
        raise DriveViewResolverError("Drive viewer did not expose a media download URL")
    try:
        media_url = json.loads(b'"' + match.group(1) + b'"')
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriveViewResolverError("Drive media URL was malformed") from exc
    view_id = DRIVE_VIEW_RE.fullmatch(urlsplit(view_url).path).group(0).split("/")[3]
    parts = urlsplit(media_url)
    values = parse_qs(parts.query)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "drive.usercontent.google.com"
        or parts.path != "/uc"
        or values.get("id") != [view_id]
        or values.get("export") != ["download"]
        or parts.username is not None
        or parts.password is not None
        or parts.port not in (None, 443)
    ):
        raise DriveViewResolverError("Drive media URL was not allowlisted")
    return media_url


def _write_bounded_response(response: requests.Response, destination: Path,
                            *, max_bytes: int, url: str) -> str:
    response.raise_for_status()
    total = 0
    with destination.open("wb") as handle:
        for chunk in response.iter_content(1 << 20):
            total += len(chunk)
            if total > max_bytes:
                raise DriveViewResolverError(
                    f"Drive media exceeded {max_bytes} bytes: {url}"
                )
            handle.write(chunk)
    if total == 0:
        raise DriveViewResolverError("Drive media response was empty")
    return response.headers.get("content-type", "").split(";", 1)[0].lower()


def _drive_download_response(url: str, *, max_redirects: int = 2) -> requests.Response:
    try:
        response = safe_get(
            HTTP, url, timeout=(15, 300), stream=True, max_redirects=max_redirects
        )
    except requests.RequestException as exc:
        raise DriveViewResolverError(f"Drive download fetch failed: {url}") from exc
    parts = urlsplit(response.url)
    if (
        parts.scheme != "https"
        or (parts.hostname or "").lower() != "drive.usercontent.google.com"
        or parts.path not in {"/uc", "/download"}
    ):
        response.close()
        raise DriveViewResolverError("Drive media redirect was not allowlisted")
    return response


def resolve_google_drive_video(url: str, destination: Path, *, max_bytes: int) -> str:
    """Download an official public Drive video, including its large-file confirmation."""
    if not is_google_drive_view_url(url):
        raise DriveViewResolverError("Drive video resolver requires a canonical view URL")
    viewer = _resolver_get(url, max_bytes=MAX_DRIVE_INTERMEDIATE_BYTES)
    if viewer.headers.get("content-type", "").split(";", 1)[0].lower() != "text/html":
        raise DriveViewResolverError("Drive video viewer response was not HTML")
    media_url = _drive_media_url(url, viewer.content)
    media_id = parse_qs(urlsplit(media_url).query)["id"][0]
    response = _drive_download_response(media_url)
    try:
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type == "text/html":
            confirmation = _bounded_response(response, media_url, MAX_DRIVE_INTERMEDIATE_BYTES)
            form = BeautifulSoup(confirmation, "html.parser").select_one(
                "form#download-form[action='https://drive.usercontent.google.com/download']"
            )
            if form is None:
                raise DriveViewResolverError("Drive video confirmation form was missing")
            fields = {
                node.get("name"): node.get("value")
                for node in form.select("input[type='hidden'][name][value]")
            }
            allowed = {"id", "export", "confirm", "uuid"}
            if (set(fields) - allowed or fields.get("id") != media_id
                    or fields.get("export") != "download"
                    or fields.get("confirm") != "t"):
                raise DriveViewResolverError("Drive video confirmation fields were invalid")
            confirmation_url = f"https://drive.usercontent.google.com/download?{urlencode(fields)}"
            response.close()
            response = _drive_download_response(confirmation_url)
        final_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if final_type == "text/html":
            raise DriveViewResolverError("Drive video confirmation did not yield media")
        return _write_bounded_response(response, destination, max_bytes=max_bytes, url=url)
    finally:
        response.close()


def load_target(conn, slug: str) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute(
            f"""select d.id as district_id,d.slug,d.name as district_name,
                       a.*,s.id as scraper_id,s.source,s.config,s.lookback_days
                  from {t('districts')} d
                  join {t('agencies')} a on a.id=d.agency_id
                  join {t('agency_scrapers')} s on s.agency_id=a.id and s.is_active
                    and (s.district_id=d.id or s.district_id is null)
                 where d.slug=%s
                 order by (s.district_id is not null) desc
                 limit 1""",
            (slug,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"no active scraper for district {slug}")
        cur.execute(
            f"""select m.id,m.source_url,m.document_filename,m.content_type,m.kind
                  from {t('agency_meetings')} m
                left join {t('district_documents')} x on x.meeting_id=m.id
                where x.district_id=%s or (m.district_id is null and m.agency_id=%s)""",
            (row["district_id"], row["id"]),
        )
        documents = cur.fetchall()
        row["seen_urls"] = {item["source_url"] for item in documents}
        row["document_identities"] = {
            identity: item["id"]
            for item in documents
            for identity in document_identities(
                item["source_url"], item.get("document_filename")
            )
        }
        # A prior collector stored Drive's viewer shell as HTML. When the
        # approved PDF resolver is later enabled, allow that exact logical
        # record one corrective fetch; URL dedup must not make a bad stored
        # representation permanent.
        row["refreshable_drive_source_urls"] = {
            item["source_url"]
            for item in documents
            if is_google_drive_view_url(item["source_url"])
            and (item.get("content_type") or "").lower() != "application/pdf"
        }
        row["refreshable_video_source_urls"] = {
            item["source_url"]
            for item in documents
            if item.get("kind") == "video"
            and (item.get("content_type") or "").lower() != "text/plain"
        }
        row["refreshable_granicus_source_urls"] = {
            item["source_url"]
            for item in documents
            if item.get("kind") == "video" and is_granicus_clip_url(item["source_url"])
        }
        return dict(row)


def start_run(conn, target: dict, trigger: str) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("select pg_advisory_xact_lock(%s)", (target["district_id"],))
        stale_after = stale_run_seconds(target)
        cur.execute(
            f"""update {t('agency_scrape_runs')}
                   set status='failed',finished_at=now(),failure_kind='interrupted',
                       error='run stopped without finalizing; reconciled before the next attempt',
                       detail=coalesce(detail,'{{}}'::jsonb) ||
                              jsonb_build_object('reconciledAt',now())
                 where district_id=%s and status='running'
                   and started_at < now()-(%s || ' seconds')::interval""",
            (target["district_id"], stale_after),
        )
        reconciled = cur.rowcount
        if reconciled:
            cur.execute(
                f"""update {t('agencies')}
                       set status='scraping_failing',
                           status_detail='one or more scrape runs stopped without finalizing',
                           consecutive_failures=consecutive_failures+%s,
                           last_attempt_at=now(),updated_at=now()
                     where id=%s""",
                (reconciled, target["id"]),
            )
            cur.execute(
                f"""update {t('scrape_schedules')}
                       set last_completed_at=now(),updated_at=now()
                     where district_id=%s""",
                (target["district_id"],),
            )
        cur.execute(
            f"""select id from {t('agency_scrape_runs')}
                 where district_id=%s and status='running'
                 order by started_at desc limit 1""",
            (target["district_id"],),
        )
        active = cur.fetchone()
        if active:
            raise RunAlreadyActive(f"district already has active scrape run {active[0]}")
        cur.execute(
            f"""insert into {t('agency_scrape_runs')}
                  (agency_id,district_id,scraper_id,trigger,status)
                values (%s,%s,%s,%s,'running') returning id""",
            (target["id"], target["district_id"], target["scraper_id"], trigger),
        )
        return cur.fetchone()[0], reconciled


def finish_run(conn, target: dict, run_id: int, *, status: str, seen: int = 0,
               new: int = 0, failure_kind: str | None = None,
               error: str | None = None, detail: dict | None = None) -> None:
    with conn.cursor() as cur:
        retry_in_24_hours = False
        if status != "ok":
            # A scheduled failure gets one delayed retry. If the preceding
            # scheduled attempt also failed, this attempt was either that
            # retry or part of an unresolved failure streak, so keep the
            # weekly next_run_at already assigned when the row was claimed.
            cur.execute(
                f"""select r.trigger,
                           (select previous.status
                              from {t('agency_scrape_runs')} previous
                             where previous.district_id=r.district_id
                               and previous.trigger='schedule'
                               and previous.id<>r.id
                               and previous.status<>'running'
                             order by previous.started_at desc,previous.id desc limit 1)
                      from {t('agency_scrape_runs')} r where r.id=%s""",
                (run_id,),
            )
            run_context = cur.fetchone()
            retry_in_24_hours = bool(
                run_context
                and run_context[0] == "schedule"
                and run_context[1] != "failed"
            )
        cur.execute(
            f"""update {t('agency_scrape_runs')}
                   set finished_at=now(),status=%s,documents_seen=%s,documents_new=%s,
                       failure_kind=%s,error=%s,detail=%s::jsonb where id=%s""",
            (status, seen, new, failure_kind, (error or "")[:4000] or None,
             json.dumps(detail or {}), run_id),
        )
        if status == "ok":
            cur.execute(
                f"""update {t('agencies')} set status='scraping_ok',status_detail=null,
                       consecutive_failures=0,last_success_at=now(),last_attempt_at=now(),updated_at=now()
                     where id=%s""", (target["id"],),
            )
        else:
            cur.execute(
                f"""update {t('agencies')} set status='scraping_failing',status_detail=%s,
                       consecutive_failures=consecutive_failures+1,last_attempt_at=now(),updated_at=now()
                     where id=%s""", ((error or status)[:1000], target["id"]),
            )
        cur.execute(
            f"""update {t('scrape_schedules')}
                   set last_completed_at=now(),
                       next_run_at=case when %s then now()+interval '24 hours'
                                        else next_run_at end,
                       updated_at=now()
                 where district_id=%s""",
            (retry_in_24_hours, target["district_id"]),
        )


def document_limit(target: dict) -> int:
    return _bounded_config_int(
        target, "document_max_bytes", MAX_DOCUMENT_BYTES, 1,
        MAX_CONFIGURED_DOCUMENT_BYTES,
    )


def video_limit(target: dict) -> int:
    return _bounded_config_int(
        target, "video_max_bytes", DEFAULT_VIDEO_MAX_BYTES, 1,
        MAX_CONFIGURED_VIDEO_BYTES,
    )


def download(url: str, destination: Path, *, max_bytes: int = MAX_DOCUMENT_BYTES,
             google_drive_view_pdf: bool = False,
             google_drive_view_media: bool = False,
             youtube_transcript_languages: list[str] | None = None,
             granicus_captions: bool = False,
             video_transcript_config: dict | None = None,
             video_max_bytes: int = DEFAULT_VIDEO_MAX_BYTES,
             metadata: dict | None = None) -> str:
    DOCUMENT_DOWNLOAD_THROTTLE.wait(url)
    try:
        if (youtube_transcript_languages is not None
                and video_id_from_url(url) is not None):
            return fetch_youtube_transcript(
                url, destination, youtube_transcript_languages,
                max_bytes=max_bytes,
            )
        if granicus_captions:
            return fetch_granicus_captions(url, destination, max_bytes=max_bytes)
        if video_transcript_config is not None:
            with tempfile.TemporaryDirectory() as temporary:
                media = Path(temporary) / "recording"
                if is_google_drive_view_url(url):
                    if video_transcript_config.get("google_drive") is not True:
                        raise DriveViewResolverError(
                            "Drive video requires config.video_transcripts.google_drive"
                        )
                    resolve_google_drive_video(url, media, max_bytes=video_max_bytes)
                else:
                    download(url, media, max_bytes=video_max_bytes)
                return fetch_video_transcript(
                    url, media, destination, video_transcript_config,
                    max_bytes=max_bytes,
                )
        if google_drive_view_pdf:
            try:
                host, _ = _strict_https_url(url)
            except DriveViewResolverError:
                host = ""
            if host == "drive.google.com":
                if not is_google_drive_view_url(url):
                    raise DriveViewResolverError(
                        "Drive resolver refuses non-canonical Drive document URLs"
                    )
                return resolve_google_drive_view(url, destination, max_bytes=max_bytes)
        if google_drive_view_media:
            if not is_google_drive_view_url(url):
                raise DriveViewResolverError(
                    "Drive media resolver requires a canonical view URL"
                )
            return resolve_google_drive_video(url, destination, max_bytes=max_bytes)
        with safe_get(HTTP, url, timeout=(15, 90), stream=True) as response:
            response.raise_for_status()
            if metadata is not None:
                disposition = Message()
                disposition["content-disposition"] = response.headers.get(
                    "content-disposition", ""
                )
                metadata["filename"] = disposition.get_filename()
            total = 0
            with destination.open("wb") as handle:
                for chunk in response.iter_content(1 << 16):
                    total += len(chunk)
                    if total > max_bytes:
                        raise RuntimeError(f"document exceeds {max_bytes} bytes: {url}")
                    handle.write(chunk)
            return response.headers.get("content-type", "").split(";", 1)[0]
    finally:
        # Failed requests still reached the host and must count toward pacing.
        DOCUMENT_DOWNLOAD_THROTTLE.finished(url)


def normalized_document_filename(value: str | None) -> str | None:
    filename = unquote(Path(value or "").name).strip().lower()
    return filename or None


# Dynamic viewer/handler-script extensions, not real document filenames.
# Several meeting-portal CMSes (Granicus's classic MediaManager --
# DocumentViewer.php, AgendaViewer.php, MinutesViewer.php, MediaPlayer.php --
# and Diligent Community's generic /File.html) serve every distinct document
# through the same fixed script path and encode the real per-document
# identity only in the query string. Treating that shared script name as a
# filename identity collapsed every such document into one false duplicate,
# confirmed independently against two districts on two different platforms.
GENERIC_SCRIPT_SUFFIXES = {
    ".php", ".php3", ".php4", ".php5", ".phtml",
    ".asp", ".aspx", ".ashx", ".jsp", ".jspx", ".cgi", ".cfm", ".html", ".htm",
}


def document_identities(url: str, content_filename: str | None = None) -> set[str]:
    """Identify a collected document by its link and, when useful, filename."""
    parts = urlsplit(url)
    canonical_url = urlunsplit((
        parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, "",
    ))
    identities = {f"url:{canonical_url}"}
    path_filename = normalized_document_filename(parts.path)
    path_suffix = Path(path_filename or "").suffix
    filenames = {path_filename}
    # Dynamic document handlers commonly return the same generic download
    # name (for example Legistar View.ashx -> Agenda.pdf) for every meeting.
    # In that case the query-bearing URL is the only reliable identity; using
    # the response filename would collapse distinct documents into one row.
    if path_suffix not in GENERIC_SCRIPT_SUFFIXES:
        filenames.add(normalized_document_filename(content_filename))
    # Generic endpoint names such as /view and /download collide heavily. A
    # suffix is a conservative signal that the URL path contains a filename
    # -- except a dynamic-script suffix, which marks a generic viewer
    # endpoint shared by every document on that CMS rather than a real
    # per-document filename; see GENERIC_SCRIPT_SUFFIXES above.
    for filename in filenames:
        if not filename:
            continue
        suffix = Path(filename).suffix
        if suffix and suffix not in GENERIC_SCRIPT_SUFFIXES:
            identities.add(f"filename:{filename}")
    return identities


def needs_google_drive_pdf_refresh(target: dict, url: str) -> bool:
    """Whether an old Drive viewer-shell record needs its canonical PDF."""
    return (
        (target.get("config") or {}).get("google_drive_view_pdf") is True
        and url in target.get("refreshable_drive_source_urls", set())
        and is_google_drive_view_url(url)
    )


def needs_video_transcript_refresh(target: dict, candidate: dict) -> bool:
    return (
        candidate.get("kind") == "video"
        and (target.get("config") or {}).get("video_transcripts", {}).get("enabled") is True
        and video_id_from_url(candidate["document_url"]) is None
        and candidate["document_url"] in target.get("refreshable_video_source_urls", set())
    )


def needs_granicus_caption_refresh(target: dict, candidate: dict) -> bool:
    return (
        candidate.get("kind") == "video"
        and (target.get("config") or {}).get("granicus_captions", {}).get("enabled") is True
        and is_granicus_clip_url(candidate["document_url"])
        and candidate["document_url"] in target.get("refreshable_granicus_source_urls", set())
    )


MAX_VIDEO_TRANSCRIPTIONS_PER_RUN = 12


def limit_video_transcriptions(target: dict, candidates: list[dict]) -> tuple[list[dict], int]:
    """Bound paid non-YouTube ASR work for one product entity and run."""
    config = target.get("config") or {}
    if config.get("video_transcripts", {}).get("enabled") is not True:
        return candidates, 0

    known = set(target.get("document_identities", {}))
    accepted: list[dict] = []
    transcription_count = 0
    skipped = 0
    for candidate in candidates:
        url = candidate["document_url"]
        identities = document_identities(url)
        needs_transcription = (
            candidate.get("kind") == "video"
            and video_id_from_url(url) is None
            and (
                identities.isdisjoint(known)
                or needs_video_transcript_refresh(target, candidate)
            )
        )
        if needs_transcription:
            if transcription_count >= MAX_VIDEO_TRANSCRIPTIONS_PER_RUN:
                skipped += 1
                continue
            transcription_count += 1
            known.update(identities)
        accepted.append(candidate)
    return accepted, skipped


def store_candidate(conn, target: dict, run_id: int, candidate: dict) -> bool:
    identities = document_identities(candidate["document_url"])
    known = target.setdefault("document_identities", {})
    existing_id = next((known[item] for item in identities if item in known), None)
    refresh_drive_pdf = needs_google_drive_pdf_refresh(target, candidate["document_url"])
    refresh_video = needs_video_transcript_refresh(target, candidate)
    refresh_granicus = needs_granicus_caption_refresh(target, candidate)
    if existing_id is not None and not refresh_drive_pdf and not refresh_video and not refresh_granicus:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""update {t('agency_meetings')}
                       set district_id=coalesce(district_id,%s),
                           meeting_date=coalesce(%s,meeting_date),
                           title=coalesce(%s,title),kind=%s,updated_at=now()
                     where id=%s""",
                (target["district_id"], candidate.get("meeting_date"),
                 candidate.get("title"), candidate.get("kind", "other"), existing_id),
            )
            cur.execute(
                f"""insert into {t('district_documents')} (district_id,meeting_id)
                     values (%s,%s) on conflict do nothing""",
                (target["district_id"], existing_id),
            )
        return False

    suffix = Path(candidate["document_url"].split("?", 1)[0]).suffix[:12]
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"document{suffix}"
        metadata: dict = {}
        reported_type = download(
            candidate["document_url"], local, max_bytes=document_limit(target),
            google_drive_view_pdf=(target.get("config") or {}).get(
                "google_drive_view_pdf", False
            ),
            google_drive_view_media=(target.get("config") or {}).get(
                "google_drive_view_media", False
            ),
            youtube_transcript_languages=(target.get("config") or {}).get(
                "youtube_transcripts", {}
            ).get("languages", ["en"])
            if candidate.get("kind") == "video"
            and (target.get("config") or {}).get(
                "youtube_transcripts", {}
            ).get("enabled") is True else None,
            granicus_captions=(
                candidate.get("kind") == "video"
                and (target.get("config") or {}).get(
                    "granicus_captions", {}
                ).get("enabled") is True
                and is_granicus_clip_url(candidate["document_url"])
            ),
            video_transcript_config=(target.get("config") or {}).get("video_transcripts")
            if candidate.get("kind") == "video"
            and (target.get("config") or {}).get("video_transcripts", {}).get("enabled") is True
            and video_id_from_url(candidate["document_url"]) is None
            else None,
            video_max_bytes=video_limit(target),
            metadata=metadata,
        )
        content_filename = normalized_document_filename(
            candidate.get("document_filename") or metadata.get("filename")
        )
        all_identities = document_identities(candidate["document_url"], content_filename)
        existing_id = next((known[item] for item in all_identities if item in known), None)
        if (existing_id is not None and not refresh_drive_pdf
                and not refresh_video and not refresh_granicus):
            with dict_cursor(conn) as cur:
                cur.execute(
                    f"""update {t('agency_meetings')}
                           set district_id=coalesce(district_id,%s),
                               meeting_date=coalesce(%s,meeting_date),
                               title=coalesce(%s,title),kind=%s,updated_at=now()
                         where id=%s""",
                    (target["district_id"], candidate.get("meeting_date"),
                     candidate.get("title"), candidate.get("kind", "other"), existing_id),
                )
                cur.execute(
                    f"""insert into {t('district_documents')} (district_id,meeting_id)
                         values (%s,%s) on conflict do nothing""",
                    (target["district_id"], existing_id),
                )
            return False
        stored = put_file(local, district=target["slug"], source_url=candidate["document_url"])
        effective_content_type = reported_type or stored["contentType"]

    with dict_cursor(conn) as cur:
        cur.execute(
            f"""insert into {t('agency_meetings')}
                  (agency_id,district_id,meeting_date,title,kind,source_url,storage_key,
                  document_filename,content_type,bytes,sha256,first_seen_run,updated_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                on conflict (agency_id,source_url) where origin='scraped' do update set
                  district_id=coalesce(excluded.district_id,{t('agency_meetings')}.district_id),
                  meeting_date=coalesce(excluded.meeting_date,{t('agency_meetings')}.meeting_date),
                  title=coalesce(excluded.title,{t('agency_meetings')}.title),kind=excluded.kind,
                  document_filename=coalesce({t('agency_meetings')}.document_filename,
                                             excluded.document_filename),
                  storage_key=excluded.storage_key,content_type=excluded.content_type,
                  bytes=excluded.bytes,sha256=excluded.sha256,
                  updated_at=now()
                returning id""",
            (target["id"], target["district_id"], candidate.get("meeting_date"),
             candidate.get("title"), candidate.get("kind", "other"),
             candidate["document_url"], stored["key"], content_filename,
             effective_content_type,
             stored["bytes"], stored["sha256"], run_id),
        )
        meeting_id = cur.fetchone()["id"]
        cur.execute(
            f"""insert into {t('district_documents')} (district_id,meeting_id)
                values (%s,%s) on conflict do nothing""",
            (target["district_id"], meeting_id),
        )
        cur.execute(
            f"""insert into {t('agency_document_versions')}
                  (meeting_id,scrape_run_id,storage_key,content_type,bytes,sha256)
                values (%s,%s,%s,%s,%s,%s)
                on conflict do nothing returning id""",
            (meeting_id, run_id, stored["key"], effective_content_type,
             stored["bytes"], stored["sha256"]),
        )
        created = bool(cur.fetchone())
        if created:
            for identity in all_identities:
                known[identity] = meeting_id
            target.setdefault("seen_urls", set()).add(candidate["document_url"])
        return created


def run_one(slug: str, trigger: str = "manual") -> dict:
    with connect() as conn:
        target = load_target(conn, slug)
        try:
            run_id, reconciled = start_run(conn, target, trigger)
        except RunAlreadyActive as exc:
            return {"ok": False, "runId": None, "error": str(exc),
                    "failureKind": "already_running"}
        # Commit the running row before executing untrusted/network work. If the
        # process is killed, the stale running row is itself an observable error.
        conn.commit()
        try:
            outcome = run_scraper(
                target["source"], target, target["seen_urls"],
                timeout=scraper_timeout(target),
                config=target.get("config") or {},
            )
            if not outcome["ok"]:
                finish_run(conn, target, run_id, status="failed",
                           seen=len(outcome.get("meetings") or []),
                           failure_kind=outcome.get("failure_kind"),
                           error=outcome.get("error"), detail={
                               "logs": outcome.get("logs", [])[-20:],
                               "warnings": outcome.get("warnings", [])[-20:],
                               "rejected": outcome.get("rejected", 0),
                           })
                return {"ok": False, "runId": run_id, "error": outcome.get("error")}

            candidates, videos_skipped = limit_video_transcriptions(
                target, outcome["meetings"]
            )
            warnings = list(outcome.get("warnings", []))
            if videos_skipped:
                warnings.append(
                    f"skipped {videos_skipped} new video recordings after the "
                    f"{MAX_VIDEO_TRANSCRIPTIONS_PER_RUN}-transcription per-run limit"
                )
            new = 0
            transcript_failures = 0
            for candidate in candidates:
                try:
                    if store_candidate(conn, target, run_id, candidate):
                        new += 1
                except YouTubeTranscriptError as exc:
                    if (candidate.get("kind") != "video"
                            or video_id_from_url(candidate["document_url"]) is None):
                        raise
                    transcript_failures += 1
                    warnings.append(
                        "skipped YouTube recording with no available transcript: "
                        f"{candidate['document_url']} ({exc})"
                    )
            finish_run(conn, target, run_id, status="ok", seen=len(outcome["meetings"]),
                       new=new, detail={"logs": outcome.get("logs", [])[-20:],
                                        "warnings": warnings[-20:],
                                        "rejected": outcome.get("rejected", 0)
                                        + videos_skipped + transcript_failures,
                                        "videoTranscriptionsSkipped": videos_skipped,
                                        "videoTranscriptionsUnavailable": transcript_failures})
            return {"ok": True, "runId": run_id, "reconciledRuns": reconciled,
                    "documentsSeen": len(outcome["meetings"]), "documentsNew": new}
        except TerminationRequested as exc:
            conn.rollback()
            finish_run(conn, target, run_id, status="failed",
                       failure_kind="interrupted", error=str(exc))
            return {"ok": False, "runId": run_id, "error": str(exc)}
        except requests.RequestException as exc:
            conn.rollback()
            finish_run(conn, target, run_id, status="failed", failure_kind="fetch_error", error=str(exc))
            return {"ok": False, "runId": run_id, "error": str(exc)}
        except Exception as exc:
            conn.rollback()
            finish_run(conn, target, run_id, status="failed", failure_kind="storage_error", error=str(exc))
            return {"ok": False, "runId": run_id, "error": str(exc)}


def due_slugs(limit: int) -> list[str]:
    with connect() as conn:
        with dict_cursor(conn) as cur:
            cur.execute(
                f"""select d.slug,q.id as schedule_id,q.cadence_days,q.schedule_config,
                           now() as claimed_at
                    from {t('scrape_schedules')} q
                    join {t('districts')} d on d.id=q.district_id
                    where q.enabled and q.next_run_at<=now()
                    order by q.next_run_at limit %s for update of q skip locked""",
                (limit,),
            )
            schedules = cur.fetchall()
            for schedule in schedules:
                if schedule["schedule_config"] is None:
                    next_run_at = schedule["claimed_at"] + timedelta(
                        days=schedule["cadence_days"]
                    )
                else:
                    next_run_at = next_run_after(
                        schedule["schedule_config"], schedule["claimed_at"]
                    )
                cur.execute(
                    f"""update {t('scrape_schedules')} set
                          last_started_at=now(),next_run_at=%s,
                          updated_at=now()
                        where id=%s""",
                    (next_run_at, schedule["schedule_id"]),
                )
            return [schedule["slug"] for schedule in schedules]


def main() -> None:
    def request_shutdown(signum, _frame):
        raise TerminationRequested(f"received signal {signum}")

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGHUP, request_shutdown)
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--district")
    mode.add_argument("--due", action="store_true")
    parser.add_argument("--trigger", choices=("manual", "schedule", "verification"), default="manual")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    slugs = due_slugs(args.limit) if args.due else [args.district]
    results = [run_one(slug, "schedule" if args.due else args.trigger) for slug in slugs]
    payload = {"ok": all(item["ok"] for item in results), "count": len(results), "results": results}
    print(json.dumps(payload))
    if not payload["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
