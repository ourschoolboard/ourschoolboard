"""Turn a bounded local video into a provenance-labelled OpenRouter transcript."""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from safe_http import safe_post


OPENROUTER_STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
DEFAULT_MODEL = "qwen/qwen3-asr-1.7b"
AUTOMATIC_FALLBACK_MODELS = (
    "qwen/qwen3-asr-0.6b",
    "nvidia/nemotron-3.5-asr-streaming-multilingual-0.6b",
)
DEFAULT_CHUNK_SECONDS = 300
DEFAULT_CONCURRENCY = 3
MAX_CONCURRENCY = 6
DEFAULT_TRANSCODE_TIMEOUT = 15 * 60
MAX_CHUNKS = 240
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._:-]+$")


class VideoTranscriptError(requests.RequestException):
    """A recording could not be safely converted into a transcript source."""


def _timestamp(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _ffmpeg_binary() -> str:
    configured = os.environ.get("FFMPEG_PATH", "").strip()
    return configured or "ffmpeg"


def extract_audio_chunks(video: Path, output_dir: Path, *, chunk_seconds: int,
                         timeout_seconds: int = DEFAULT_TRANSCODE_TIMEOUT) -> list[Path]:
    """Extract mono 16 kHz MP3 chunks using a fixed, non-shell command."""
    pattern = output_dir / "audio-%05d.mp3"
    command = [
        _ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "file,pipe", "-threads", "1",
        "-i", str(video), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000",
        "-b:a", "48k", "-f", "segment", "-segment_time", str(chunk_seconds),
        "-reset_timestamps", "1", str(pattern),
    ]
    try:
        completed = subprocess.run(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, check=False, timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise VideoTranscriptError("FFmpeg is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoTranscriptError("video audio extraction timed out") from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise VideoTranscriptError(f"FFmpeg could not extract video audio: {detail}")
    chunks = sorted(output_dir.glob("audio-*.mp3"))
    if not chunks:
        raise VideoTranscriptError("video contains no readable audio track")
    if len(chunks) > MAX_CHUNKS:
        raise VideoTranscriptError(f"video exceeds the {MAX_CHUNKS}-chunk limit")
    return chunks


def transcribe_audio(audio: Path, *, model: str, language: str | None,
                     session: requests.Session | None = None) -> tuple[str, dict]:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise VideoTranscriptError("OPENROUTER_API_KEY is not configured")
    if not MODEL_RE.fullmatch(model) or len(model) > 120:
        raise VideoTranscriptError("OpenRouter transcription model is invalid")
    payload = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(audio.read_bytes()).decode("ascii"),
            "format": "mp3",
        },
        "temperature": 0,
    }
    if language:
        payload["language"] = language
    client = session or requests.Session()
    try:
        response = safe_post(
            client, OPENROUTER_STT_URL,
            headers={
                "authorization": f"Bearer {key}",
                "content-type": "application/json",
                "http-referer": "https://ourschoolboard.org",
                "x-title": "Our School Board meeting transcription",
            },
            data=json.dumps(payload), timeout=(15, 90),
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        detail = str(exc).replace(key, "[redacted]")
        raise VideoTranscriptError(f"OpenRouter transcription failed: {detail}") from exc
    text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()
    if len(text) < 2:
        raise VideoTranscriptError("OpenRouter returned an empty transcription")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return text, usage


def transcription_models(primary: str) -> list[str]:
    """Return the global ordered model chain without retrying a duplicate."""
    return [primary, *(
        candidate for candidate in AUTOMATIC_FALLBACK_MODELS
        if candidate != primary
    )]


def render_transcript(source_url: str, chunks: list[str], *, model: str,
                      language: str | None, chunk_seconds: int,
                      usage: list[dict],
                      chunk_models: list[str] | None = None) -> str:
    fallbacks = transcription_models(model)[1:]
    selected_models = chunk_models or [model] * len(chunks)
    lines = [
        "Board meeting video transcript",
        f"Source recording: {source_url}",
        f"Primary transcription model: {model}",
        f"Automatic fallback transcription models: {', '.join(fallbacks) or 'none'}",
        f"Requested language: {language or 'automatic detection'}",
        f"Audio chunks: {len(chunks)} at up to {chunk_seconds} seconds each",
        "Transcript type: machine-generated speech recognition; verify against the recording.",
        "Timestamps mark the start offset of each independently transcribed chunk.",
    ]
    seconds = sum(float(item.get("seconds") or 0) for item in usage)
    cost = sum(float(item.get("cost") or 0) for item in usage)
    if seconds:
        lines.append(f"Provider-reported audio seconds: {seconds:.1f}")
    if cost:
        lines.append(f"Provider-reported cost USD: {cost:.6f}")
    lines.append("")
    for index, text in enumerate(chunks):
        lines.extend((
            f"[{_timestamp(index * chunk_seconds)}] [{selected_models[index]}]",
            text, "",
        ))
    return "\n".join(lines).rstrip() + "\n"


def fetch_to_file(source_url: str, video: Path, destination: Path, config: dict,
                  *, max_bytes: int) -> str:
    model = str(config.get("model") or DEFAULT_MODEL)
    language = config.get("language")
    chunk_seconds = int(config.get("chunk_seconds") or DEFAULT_CHUNK_SECONDS)
    concurrency = int(config.get("concurrency") or DEFAULT_CONCURRENCY)
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise VideoTranscriptError(
            f"video transcription concurrency must be 1-{MAX_CONCURRENCY}"
        )
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        audio = extract_audio_chunks(video, root, chunk_seconds=chunk_seconds)

        def transcribe(chunk: Path) -> tuple[str, dict, str]:
            with requests.Session() as client:
                client.mount("https://", HTTPAdapter(max_retries=Retry(
                    total=3, connect=2, read=2, status=3, backoff_factor=1,
                    status_forcelist=(429, 500, 502, 503, 504),
                    allowed_methods=frozenset({"POST"}),
                    respect_retry_after_header=True,
                )))
                failures = []
                for candidate in transcription_models(model):
                    try:
                        text, usage = transcribe_audio(
                            chunk, model=candidate, language=language, session=client
                        )
                        return text, usage, candidate
                    except VideoTranscriptError as exc:
                        failures.append(f"{candidate}: {exc}")
                raise VideoTranscriptError(
                    "all transcription models failed (" + "; ".join(failures) + ")"
                )

        # map preserves input order even though provider requests overlap, so
        # rendered timestamps remain chronological and deterministic.
        with ThreadPoolExecutor(max_workers=min(concurrency, len(audio))) as pool:
            results = list(pool.map(transcribe, audio))
        transcripts = [item[0] for item in results]
        usages = [item[1] for item in results]
        selected_models = [item[2] for item in results]
    rendered = render_transcript(
        source_url, transcripts, model=model, language=language,
        chunk_seconds=chunk_seconds, usage=usages,
        chunk_models=selected_models,
    ).encode("utf-8")
    if len(rendered) > max_bytes:
        raise VideoTranscriptError(f"video transcript exceeds {max_bytes} bytes")
    destination.write_bytes(rendered)
    return "text/plain"
