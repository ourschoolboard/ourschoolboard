"""Turn a public YouTube recording's captions into a stable source document."""
from __future__ import annotations

import html
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import requests

from safe_http import safe_get, safe_post

# Bright Data "YouTube video" dataset. Preferred transcript path: YouTube's
# own caption endpoints block datacenter IPs, and the residential proxy zone
# refuses to tunnel youtube.com, so direct collection from a hosted box almost
# never succeeds. The Datasets API is billed per video record.
DATASETS_API = "https://api.brightdata.com/datasets/v3"
YOUTUBE_DATASET_ID = "gd_lk56epmy2i5g7lzu0k"
DATASET_POLL_SECONDS = 15
DATASET_TIMEOUT_SECONDS = 480


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_INPUT_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
})
YOUTUBE_API_HOST = "www.youtube.com"
YOUTUBE_API_PATHS = frozenset({"/watch", "/youtubei/v1/player", "/api/timedtext"})


class YouTubeTranscriptError(requests.RequestException):
    """A recording could not be safely converted into a transcript source."""


def video_id_from_url(url: str) -> str | None:
    """Return an ID only for canonical, individual public YouTube video URLs."""
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or host not in YOUTUBE_INPUT_HOSTS
            or parts.username or parts.password or port not in (None, 443)):
        return None
    if host == "youtu.be":
        candidate = parts.path.strip("/") if parts.path.count("/") <= 1 else ""
    elif parts.path == "/watch":
        values = parse_qs(parts.query).get("v", [])
        candidate = values[0] if len(values) == 1 else ""
    elif parts.path.startswith(("/live/", "/shorts/")):
        candidate = parts.path.split("/", 2)[2].strip("/")
    else:
        candidate = ""
    return candidate if VIDEO_ID_RE.fullmatch(candidate) else None


def _allowed_api_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() == YOUTUBE_API_HOST
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
        and parts.path in YOUTUBE_API_PATHS
    )


class RestrictedYouTubeClient:
    """Small requests-compatible client restricted to transcript endpoints."""

    def __init__(self, proxy_url: str | None = None):
        self._session = requests.Session()
        self._session.trust_env = False
        if proxy_url:
            self._session.proxies.update({"http": proxy_url, "https": proxy_url})
        self._uses_proxy = bool(proxy_url)
        self.headers = self._session.headers
        self.cookies = self._session.cookies
        self.proxies = self._session.proxies

    def get(self, url: str, **kwargs):
        if not _allowed_api_url(url):
            raise YouTubeTranscriptError(
                "transcript library requested a non-allowlisted URL"
            )
        kwargs.setdefault("timeout", (15, 90))
        if self._uses_proxy:
            return self._proxied("get", url, **kwargs)
        return safe_get(self._session, url, max_redirects=0, **kwargs)

    def post(self, url: str, **kwargs):
        if not _allowed_api_url(url):
            raise YouTubeTranscriptError(
                "transcript library requested a non-allowlisted URL"
            )
        kwargs.setdefault("timeout", (15, 90))
        if self._uses_proxy:
            return self._proxied("post", url, **kwargs)
        return safe_post(self._session, url, **kwargs)

    def _proxied(self, method: str, url: str, **kwargs):
        response = getattr(self._session, method)(url, allow_redirects=False, **kwargs)
        if response.is_redirect or response.is_permanent_redirect:
            response.close()
            raise YouTubeTranscriptError(
                "YouTube transcript endpoint redirected unexpectedly"
            )
        return response


def _brightdata_proxy_url() -> str | None:
    server = os.environ.get("BRIGHTDATA_PROXY", "").strip()
    username = os.environ.get("BRIGHTDATA_PROXY_USER", "")
    password = os.environ.get("BRIGHTDATA_PROXY_PASS", "")
    if not any((server, username, password)):
        return None
    if not all((server, username, password)):
        raise YouTubeTranscriptError(
            "Bright Data transcript proxy is incompletely configured"
        )
    if "://" not in server:
        server = f"http://{server}"
    parts = urlsplit(server)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.path not in {"", "/"}
            or parts.query or parts.fragment):
        raise YouTubeTranscriptError("Bright Data transcript proxy server is invalid")
    try:
        port = parts.port
    except ValueError as exc:
        raise YouTubeTranscriptError(
            "Bright Data transcript proxy server is invalid"
        ) from exc
    authority = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    if port:
        authority += f":{port}"
    return (
        f"{parts.scheme}://{quote(username, safe='')}:{quote(password, safe='')}"
        f"@{authority}"
    )


def _timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render_transcript(source_url: str, transcript) -> str:
    """Create deterministic, review-friendly text from a fetched transcript."""
    transcript_type = "YouTube auto-generated captions" if transcript.is_generated \
        else "Publisher-provided captions"
    lines = [
        "YouTube board meeting transcript",
        f"Source recording: {source_url}",
        f"Video ID: {transcript.video_id}",
        f"Language: {transcript.language} ({transcript.language_code})",
        f"Transcript type: {transcript_type}",
        "Timestamps are offsets from the start of the recording.",
        "",
    ]
    for snippet in transcript:
        text = re.sub(r"\s+", " ", html.unescape(snippet.text)).strip()
        if text:
            lines.append(f"[{_timestamp(snippet.start)}] {text}")
    if len(lines) == 7:
        raise YouTubeTranscriptError("YouTube returned an empty transcript")
    return "\n".join(lines) + "\n"


def _redact(detail: str) -> str:
    for secret in (
        os.environ.get("BRIGHTDATA_API_TOKEN", ""),
        os.environ.get("BRIGHTDATA_PROXY_USER", ""),
        os.environ.get("BRIGHTDATA_PROXY_PASS", ""),
    ):
        if secret:
            detail = detail.replace(secret, "[redacted]")
    return detail


def _render_dataset_transcript(source_url: str, video_id: str, record: dict) -> str:
    """Render a Datasets API record in the same deterministic shape as
    render_transcript, so both paths produce interchangeable documents."""
    language = str(record.get("transcription_language") or "")
    if not language:
        listed = record.get("transcript_language")
        if isinstance(listed, list) and listed and isinstance(listed[0], dict):
            language = str(listed[0].get("language") or "")
    language = language or "unknown"
    # Without an affirmative statement that captions were publisher-provided,
    # label them as auto-generated: the cautious label for review purposes.
    transcript_type = ("Publisher-provided captions"
                       if language != "unknown"
                       and "auto-generated" not in language.lower()
                       else "YouTube auto-generated captions (assumed)")
    if "auto-generated" in language.lower():
        transcript_type = "YouTube auto-generated captions"
    lines = [
        "YouTube board meeting transcript",
        f"Source recording: {source_url}",
        f"Video ID: {video_id}",
        f"Language: {language}",
        f"Transcript type: {transcript_type}",
        "Timestamps are offsets from the start of the recording.",
        "",
    ]
    segments = record.get("formatted_transcript")
    if isinstance(segments, list) and segments:
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            text = re.sub(r"\s+", " ", html.unescape(str(seg.get("text", "")))).strip()
            if text:
                lines.append(f"[{_timestamp(float(seg.get('start_time', 0)) / 1000)}] {text}")
    else:
        flat = re.sub(r"\s+", " ", html.unescape(str(record.get("transcript", "")))).strip()
        if flat:
            lines[5] = "Continuous transcript; the source provides no timestamps."
            lines.append(flat)
    if len(lines) == 7:
        raise YouTubeTranscriptError("dataset returned an empty transcript")
    return "\n".join(lines) + "\n"


def _fetch_via_dataset(source_url: str, video_id: str, token: str) -> str:
    """Fetch captions through Bright Data's YouTube dataset (async trigger)."""
    session = requests.Session()
    session.trust_env = False
    session.headers["Authorization"] = f"Bearer {token}"
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    try:
        resp = session.post(
            f"{DATASETS_API}/trigger",
            params={"dataset_id": YOUTUBE_DATASET_ID, "include_errors": "true"},
            json=[{"url": canonical, "country": "US"}],
            timeout=(15, 60),
        )
        resp.raise_for_status()
        snapshot_id = str(resp.json().get("snapshot_id") or "")
        if not snapshot_id:
            raise YouTubeTranscriptError("dataset trigger returned no snapshot id")
        deadline = time.monotonic() + DATASET_TIMEOUT_SECONDS
        while True:
            progress = session.get(
                f"{DATASETS_API}/progress/{snapshot_id}", timeout=(15, 60)
            )
            progress.raise_for_status()
            status = str(progress.json().get("status") or "")
            if status == "ready":
                break
            if status in {"failed", "error"}:
                raise YouTubeTranscriptError(f"dataset snapshot {status}")
            if time.monotonic() >= deadline:
                raise YouTubeTranscriptError(
                    f"dataset snapshot not ready within {DATASET_TIMEOUT_SECONDS}s"
                )
            time.sleep(DATASET_POLL_SECONDS)
        result = session.get(
            f"{DATASETS_API}/snapshot/{snapshot_id}",
            params={"format": "json"}, timeout=(15, 120),
        )
        result.raise_for_status()
        payload = result.json()
    except YouTubeTranscriptError:
        raise
    except Exception as exc:
        raise YouTubeTranscriptError(
            f"dataset transcript fetch failed: {_redact(str(exc))}"
        ) from exc
    records = payload if isinstance(payload, list) else [payload]
    record = next((r for r in records if isinstance(r, dict)), None)
    if not record:
        raise YouTubeTranscriptError("dataset snapshot held no records")
    if not (record.get("transcript") or record.get("formatted_transcript")):
        snapshot_detail = str(
            record.get("error") or record.get("warning") or "no transcript field"
        )
        try:
            direct = session.post(
                f"{DATASETS_API}/scrape",
                params={
                    "dataset_id": YOUTUBE_DATASET_ID,
                    "format": "json",
                    "include_errors": "true",
                },
                json=[{"url": canonical, "country": "US"}],
                timeout=(15, 120),
            )
            direct.raise_for_status()
            direct_payload = direct.json()
            direct_records = direct_payload if isinstance(direct_payload, list) \
                else [direct_payload]
            record = next(
                (r for r in direct_records if isinstance(r, dict)), None
            )
        except Exception as exc:
            raise YouTubeTranscriptError(
                "dataset snapshot had no transcript and direct video check failed: "
                f"snapshot={_redact(snapshot_detail)}; direct={_redact(str(exc))}"
            ) from exc
        if not record or not (
            record.get("transcript") or record.get("formatted_transcript")
        ):
            direct_detail = str(
                (record or {}).get("error")
                or (record or {}).get("warning")
                or "no transcript field"
            )
            raise YouTubeTranscriptError(
                "dataset snapshot and direct video check had no transcript: "
                f"snapshot={_redact(snapshot_detail)}; "
                f"direct={_redact(direct_detail)}"
            )
    return _render_dataset_transcript(source_url, video_id, record)


def fetch_to_file(source_url: str, destination: Path, languages: list[str],
                  *, max_bytes: int) -> str:
    """Fetch public captions, preserving the recording URL as source provenance.

    Preferred path: Bright Data's YouTube dataset (works from datacenter IPs).
    Fallback: YouTube's own caption endpoints, which usually block hosted boxes
    but remain useful where egress looks residential or the token is absent.
    """
    video_id = video_id_from_url(source_url)
    if not video_id:
        raise YouTubeTranscriptError(
            "YouTube transcript input must be an individual video URL"
        )
    dataset_error = None
    token = os.environ.get("BRIGHTDATA_API_TOKEN", "").strip()
    if token:
        try:
            rendered = _fetch_via_dataset(source_url, video_id, token)
            return _write_rendered(rendered, destination, max_bytes)
        except YouTubeTranscriptError as exc:
            dataset_error = str(exc)
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        api = YouTubeTranscriptApi(
            http_client=RestrictedYouTubeClient(_brightdata_proxy_url())
        )
        transcript = api.fetch(video_id, languages=languages)
        rendered = render_transcript(source_url, transcript)
    except YouTubeTranscriptError as exc:
        raise _with_dataset_context(exc, video_id, dataset_error)
    except Exception as exc:
        detail = _redact(str(exc))
        raise _with_dataset_context(
            YouTubeTranscriptError(
                f"YouTube transcript unavailable for video {video_id}: {detail}"
            ), video_id, dataset_error,
        ) from exc
    return _write_rendered(rendered, destination, max_bytes)


def _with_dataset_context(exc: YouTubeTranscriptError, video_id: str,
                          dataset_error: str | None) -> YouTubeTranscriptError:
    if not dataset_error:
        return exc
    return YouTubeTranscriptError(
        f"both transcript paths failed for video {video_id} — "
        f"dataset: {dataset_error}; direct: {exc}"
    )


def _write_rendered(rendered: str, destination: Path, max_bytes: int) -> str:
    encoded = rendered.encode("utf-8")
    if len(encoded) > max_bytes:
        raise YouTubeTranscriptError(
            f"YouTube transcript exceeds {max_bytes} bytes"
        )
    destination.write_bytes(encoded)
    return "text/plain"
