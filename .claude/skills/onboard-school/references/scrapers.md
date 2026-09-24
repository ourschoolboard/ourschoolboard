# Repeatable scraper contract

## Contents

- Inputs and outputs
- Discovery and backfill
- Incremental behavior
- Failure behavior
- Testing checklist

## Inputs and outputs

Define exactly one entry point:

```python
def scrape(ctx):
    ...
```

Available inputs:

- `ctx.agency`: governing agency identity and URLs
- `ctx.website`, `ctx.board_page_url`: convenience aliases
- `ctx.config`: registered JSON config; put source pages, platform ids, and
  non-secret selectors here
- `ctx.get(url, **kwargs)`: rate-limited HTTP GET with identifying user agent,
  timeout, fetch-count limit, and response-size limit
- `ctx.browser_get(url, wait_until="domcontentloaded", selector=None, actions=None)`:
  opt-in, bounded Chromium rendering for an approved public page whose document
  links require JavaScript, or whose normal `ctx.get()` response is HTTP 403 or
  406. Record that status in the source notes before using the fallback.
  Registration must set `config.browser.enabled` to true. HTTP remains the
  default; do not use this to bypass terms, authentication, or a continuing
  document-host block.

  `config.browser.provider` selects the runner's explicit egress mode. It never
  changes providers automatically:
  - `"isp_proxy"` (default) — a plain Chromium session tunneled through the
    operator's ISP proxy. Try this first.
  - `"scraping_browser"` — a stronger, session-based remote browser, for a
    source that stays blocked even through a real proxied Chromium render.
    Not a bigger hammer for a JS-only page or a source `isp_proxy` hasn't
    actually been tried against — record why `isp_proxy` failed first.
  - `"direct"` — local sandbox-owned Chromium with no Bright Data proxy. Use
    only for an approved public government or school-district source when a
    recorded proxied attempt is rejected because Bright Data naively blocks
    certain domains. This retains the same public-IP checks, request and response limits,
    media/WebSocket blocking, declarative actions, and sandbox boundary. It is
    not an automatic fallback and cannot bypass authentication or terms.

  `actions` is an optional list of at most 30 steps, each a single-key mapping,
  run in order right after navigation and before `selector` (if given) and the
  final content read:
  - `{"click": "css-or-text-selector"}`
  - `{"fill": ["css-or-text-selector", "value"]}`
  - `{"press": "Enter"}`
  - `{"wait_for_selector": "css-or-text-selector"}`
  - `{"wait_ms": 250}` (1-10000)

  Use this when a source's real content only appears after a click or a
  form/postback-driven control — a fiscal-year dropdown wired to an
  ASP.NET-style postback, for example — not merely after JavaScript finishes
  running (that case needs no `actions`, just `browser_get(url)`). Available
  with either provider. It stays a short, declarative vocabulary the trusted
  runner executes itself, not a live page handle — the scraper describes
  intent, never drives arbitrary browser automation, and it still cannot force
  through a document host that keeps blocking every request regardless of
  how the listing page was reached.
- `ctx.already_have(url)`: optional optimization hint only
- `ctx.log(...)`: bounded structured run log

The only output is:

```python
ctx.emit_meeting(
    document_url="https://official.example/meeting.pdf",
    meeting_date="2026-08-01",
    title="Regular Board Meeting",
    kind="minutes",
)
```

Kinds are `agenda`, `minutes`, `packet`, `video`, and `other`. Emit official
HTTP(S) URLs only. Never write files, databases, object storage, email, or
subprocess output from generated code.

## Discovery and backfill

Design from stable index pages inward:

1. Fetch every configured source page.
2. Follow pagination/archive links until reaching records older than the
   configured trailing-year boundary.
3. Parse dates from structured metadata first, then visible labels, then stable
   filenames. When none of those supplies a reliable date, emit the document
   with `meeting_date=None` and log it as undated; never discard an otherwise
   valid official document solely because its date is missing or ambiguous,
   especially if the document appears to be recent. Do not infer a date from
   fetch time.
4. Resolve relative links with `urllib.parse.urljoin`.
5. Inspect meeting and agenda-item pages for nested links to minutes, packets,
   exhibits, and presentations; emit each substantive official document rather
   than stopping at the top-level meeting page.
6. Deduplicate candidate URLs inside the script before emitting.
7. Log page counts and oldest/newest dates so coverage is auditable.

The first manual run and later weekly runs use the same code. Do not maintain a
separate backfill script; two discovery paths drift.

## Incremental behavior

Emit the full bounded listing on every run, including already-known URLs. This
proves selectors still work and allows the trusted parent to hash a document
again when a publisher revises bytes at the same URL. The parent handles URL
and content deduplication.

Do not stop pagination merely because one URL is already known: meeting portals
may insert late or revised records behind the newest page. Stop only at a
date-based boundary or an explicitly documented archive terminus.

Keep changing values in config rather than code:

```json
{
  "source_pages": ["https://district.example/board/meetings"],
  "archive_pages": ["https://district.example/board/archive"],
  "platform_id": null,
  "selectors": {"row": ".meeting", "document": "a.minutes"}
}
```

Large or volatile official archives may use these bounded executor options:

```json
{
  "timeout_seconds": 900,
  "stale_run_seconds": 7200,
  "document_max_bytes": 104857600,
  "stable_pdf_fingerprint": true,
  "google_drive_view_pdf": true
}
```

Official non-YouTube recordings may opt into bounded transcription:

```json
{
  "video_transcripts": {
    "enabled": true,
    "model": "qwen/qwen3-asr-1.7b",
    "language": "en",
    "chunk_seconds": 300,
    "concurrency": 3,
    "google_drive": true
  },
  "video_max_bytes": 536870912
}
```

The input must be an individual, public, directly downloadable recording in a
format FFmpeg can decode. `google_drive` is limited to canonical public
`https://drive.google.com/file/d/<ID>/view` links and requires compliance
checks for both `drive.google.com` and `drive.usercontent.google.com`. The
trusted parent resolves the exact file id, extracts mono audio in bounded
chunks, sends raw base64 audio to OpenRouter's speech-to-text endpoint, and
stores a labelled machine transcript. The default model is
`qwen/qwen3-asr-1.7b`; three chunks are transcribed concurrently by default
and the configurable concurrency is bounded to 1-6. At most 12 new arbitrary
videos are transcribed for one district or school in one run; excess candidates
are recorded as rejected and remain eligible for a later run. Empty model output, missing audio, oversized media,
unapproved Drive hosts, FFmpeg failure, and provider errors fail the run.

- `timeout_seconds` raises discovery time only when the default 300 seconds is
  insufficient; it is capped at 1,800 seconds.
- `stale_run_seconds` controls when a killed run can be reconciled before a new
  attempt; it is never allowed below twice the discovery timeout.
- `document_max_bytes` is capped at 250 MiB.
- `stable_pdf_fingerprint` is only for official PDF export URLs proven to
  rewrite metadata while preserving extracted text. Accepted originals remain
  byte-exact. PDFs containing images conservatively retain byte comparison.
- `google_drive_view_pdf` is only for canonical public
  `https://drive.google.com/file/d/<ID>/view` document URLs. The trusted
  executor follows Google's robots-allowed viewer manifest to the allowlisted
  `doc-*-a0-apps-viewer.googleusercontent.com` PDF and stores those original
  PDF bytes while retaining the canonical Drive view URL as `sourceUrl`.
  Registration requires non-prohibiting platform terms records for both
  `drive.google.com` and `googleusercontent.com`; run compliance checks for
  both hosts first. Every resolver request is robots-checked and crawl-delayed,
  and malformed, oversized, redirected, or non-PDF responses fail closed.

Do not use Drive `/uc`, `/open`, or hand-copied signed viewer URLs. Do not use
this option merely to suppress changing HTML hashes: a successful initial run
must store `application/pdf` bytes, and its immediate repeat must report zero
new versions.

Known symptom: an immediate repeat run keeps reporting a large "new" count for
an unchanged source. The cause is usually byte-instability in on-the-fly PDF
rendering (a metadata byte differs on every fetch), not actual new documents.
Documents hosted on `drive.google.com` or `docs.google.com` are versioned by
URL alone (owner decision, 2026-08-19: same link is the same file), so the
executor already dedupes them cleanly. For other hosts with this symptom,
confirm the documents are text PDFs, enable `stable_pdf_fingerprint`,
re-register, and require a clean zero-new repeat run before accepting the
corpus — otherwise every weekly run stores duplicates.

Use the smallest override that passes a complete initial run. Registration
rejects invalid or unbounded values.

Never put credentials, cookies, tokens, or personal data in config.

## Failure behavior

Raise an exception for:

- any required source page that cannot be fetched;
- non-success HTTP responses;
- a selector that yields no records;
- pagination loops or a configured page cap being reached;
- an archive whose newest/oldest dates make the expected window incomplete;
- malformed structured data that prevents reliable document URLs. A missing or
  ambiguous date by itself is not a malformed row: emit it with
  `meeting_date=None` and include it in the run's undated count.

Do not catch an exception merely to log it and return success. A clean exit with
zero emitted documents is also classified as `empty_result`. The trusted
executor records all failures in `agency_scrape_runs`, sets `failure_kind`, and
increments the governing agency's consecutive failure count.

Do not launch a second copy while a run is active. The executor rejects
concurrent runs for one district and reconciles an old `running` row only after
its bounded stale window. SIGINT/SIGTERM are recorded as `interrupted`; a hard
kill is reconciled on the next attempt.

One malformed optional row may be logged and skipped only when other rows prove
the source itself is healthy. Include a count so the loss is visible.

## Testing checklist

1. Exercise the registered scraper with the initial manual run against its
   stable live pages; do not build a separate discovery implementation.
2. Confirm emitted URLs, kinds, and dates exactly.
3. Confirm the oldest emitted known date reaches the 12-month boundary when the
   archive contains records that old, and separately inspect every undated record
   which appears highly relevant or likely to be recent. Undated records remain
   part of the collected corpus.
4. Run twice; the second run must succeed with documents seen and usually zero
   new versions.
5. Change a selector to a nonexistent value; require nonzero exit and a stored
   `parse_error` or `empty_result` run.
6. Change a source URL to an unreachable one; require a stored `fetch_error`.
7. Restore the working version and rerun before enabling the schedule.
