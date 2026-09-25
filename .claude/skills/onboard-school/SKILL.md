---
name: onboard-school
description: Onboard or update a public school, charter, or school district in ourschoolboard.org by researching its governing body and sources, backfilling the latest 12 months plus posted upcoming agendas, publishing page content and alerts through narrow storage tools, and registering a repeatable board-aware scraper schedule. Use whenever asked to add, onboard, research, refresh, or build a page/feed/overview for a school or district. Do not use for private schools.
---

# Onboard a school or district

Produce a source-backed page now and leave behind a board-aware collection path. Do
not create a branch or PR. Use only these write capabilities:

- Page content and alerts: `python3 scripts/tools/page_store.py`
- Past meeting/publication reports: `python3 scripts/tools/past_report_store.py`
- Local votes and board elections: `python3 scripts/tools/vote_store.py`
- Source bytes: `python3 scripts/tools/spaces_store.py`
- Source text extraction: `python3 scripts/tools/source_text.py`
- Scraper registration: `python3 scripts/tools/scraper_store.py`
- Manual and scheduled execution: `python3 scripts/pipeline/execute.py`
- Compliance checks: `python3 scripts/pipeline/compliance.py`

These tools own credentials, validate inputs, and limit which records can
change. Temporary research files may be written locally; publishable state must
end in Postgres and source documents must end in the configured S3-compatible
object store.

## 1. Establish the entity

Confirm the exact public school or district, address, stable NCES identifier,
governing district, and official website. Stop for a private school.

For a school inside a large district, determine empirically whether the board
ever names the school. If it does not, prioritize school-site councils, school
plans, allocation tables, and other school-specific records over district-wide
board activity.

Read [references/sources.md](references/sources.md) before choosing sources and
[references/compliance.md](references/compliance.md) before fetching them.

Create a district descriptor JSON. For a new entity, store it as a draft:

```sh
python3 scripts/tools/page_store.py put-district /tmp/district.json
```

When an official board directory is public, also extract its current member
roster and public contact channels into `schoolBoardMembersList` and
`schoolBoardContactInfo`, including each member's `seatType`, `firstElected`,
and a one-to-two-sentence `background` taken only from an official bio. Record
only information explicitly published by the district; never infer
email-address patterns or personal contact details. These fields are optional
and their absence does not block onboarding.

For an update to an already-published district, preserve the current district
and page statuses while researching. Prepare replacements in `/tmp` and write
them only after they pass the publication checks; never turn a live page into a
draft merely to begin a refresh.

Read [references/storage.md](references/storage.md) for accepted JSON shapes,
publication states, and what each tool is allowed to change.

Set `agencyLeaid` to the governing agency's NCES LEA id. A school page and its
governing agency are different concepts; do not invent a second LEA.

Run the compliance tool against the governing agency and every distinct source
host before registering a scraper. Registration refuses an agency that recorded
prohibiting terms.

```sh
python3 scripts/pipeline/compliance.py --leaid <agency-leaid> \
  --url <official-site> --platform-url <document-host>
```

If the board website is inaccessible, an officially linked YouTube channel is a
strong fallback. Use the configured Bright Data YouTube channel endpoint to
list individual recordings, then its per-video check to retrieve available
transcripts; retain each canonical recording URL as the source.


## 2. Identify durable source pages

Find the pages the weekly scraper can revisit, not only today's document URLs.
Record each index/archive page, platform id, pagination behavior, date coverage,
and evidence that the last 12 months are complete. Prefer stable official APIs
and archive pages over search results or hand-collected links, but hand collected-links are fine for less well organized websites.

Look for meeting minutes, meeting recordings/transcripts, agenda documents, budget documents, and other critical sources where information about school governance is shared, e.g. a principal's weekly letter to parents, slide presentations or planning documents, news about the district, or documents covering the district which are stored on a state government website.

Alongside this durable-source discovery, look for the entity's current official
phone or personal-device policy and its AI or generative-AI policy or guidance.
Limit that lookup to the available likely official repositories: the policy
manual; current student and employee handbooks; technology or acceptable-use
rules; and official guidance or search pages. Prefer a school-specific policy
when one exists; for a school governed by a district, retain a districtwide
policy with the appropriate scope. Do not relabel a generic acceptable-use
policy as an AI policy unless the source actually addresses AI. Include found
policy source(s) in the initial collector with the meeting sources; do not start
a later, separate policy discovery or scraper pass. After checking those
available repositories, record either the found source(s) or a coverage
limitation and continue. Missing policies never block normal onboarding or
publication.

During this same durable meeting-source discovery, check for official agendas
or agenda packets already posted for future meetings, normally within the next
90 days. Include them in the initial collection from that same source. Do not
run a later, separate agenda-discovery pass.

When you are in this step, you are encouraged to read some of the documents yourself in order to find pointers to other sources, decide what additional supplemental information might be useful to find, and begin thinking about what alerts to generate.

Treat a source that returns zero documents as broken unless the source itself
provides affirmative evidence that the archive is empty. A quiet scraper and a
quiet district must never look identical.

## 3. Backfill the trailing 12 months and posted upcoming agendas

Use the same discovery logic intended for the weekly scraper. The initial run
must cover from today minus 12 months through today, including agendas,
minutes, packets, budgets, plans, and other sources selected for this entity.
Also collect every official agenda or agenda packet identified in Step 2 for a
future meeting, normally within the next 90 days. A calendar entry without an
attached agenda is not enough to create a meeting report. The recurring scraper
must continue checking that durable source so newly posted agendas and later
minutes are collected.

Collect each phone/device or AI/generative-AI policy found in Step 2 even when
its adoption or revision date falls before the trailing-year window. These
current-policy documents are a deliberate exception to the normal backfill
boundary. If no reliable adoption or revision date is published, retain the
document with a null date rather than guessing.

Store every original document in the configured object store and its metadata in the database. Do
not commit raw files, extracted text, page JSON, or a corpus manifest. The
trusted executor performs both writes after the scraper emits candidates.
Do not exclude an otherwise valid official document merely because its exact
date is absent or ambiguous. Emit and retain it with a null date, then account
for it explicitly during document review.

## 4. Write the repeatable scraper

Identify the meeting CMS before writing code. Read
[references/prebuilt-scrapers.md](references/prebuilt-scrapers.md) and use a
listed adapter whenever the detected platform and required configuration match.
Configure and test the adapter; do not generate a district-specific copy of an
existing platform scraper.

If no supported adapter is listed, continue with a generated scraper. Do not
mistake a legacy district-specific fetcher for a reusable adapter merely because
it accesses the same CMS.

Read [references/scrapers.md](references/scrapers.md) completely and start from
[assets/scraper_template.py](assets/scraper_template.py). Customize discovery,
pagination, and date parsing for the identified pages.

The script must define `scrape(ctx)`, fetch through `ctx.get()` by default, and
emit through `ctx.emit_meeting()`. For an approved public page whose links truly
require JavaScript, or whose normal `ctx.get()` response is HTTP 403 or 406, it
may use `ctx.browser_get()` after registration opts in with bounded
`config.browser` settings. Record the failed normal status in the source notes
first. Browser-enabled runs require the operator-configured Bright Data proxy
and never fall back to direct Chromium egress. This fallback is only for the
fixed public source page: retrieve its discovered documents through `ctx.get()`
when their official hosts are reachable, and do not use it to bypass
prohibiting terms or authentication. It must
not import Playwright,
database, object-storage, socket, or process-execution libraries. Emit every
document visible in the bounded archive on each run; the trusted parent
performs deduplication and detects revised bytes at a stable URL.

If the ISP-proxied `browser_get()` still cannot reach a source page — a
bot-mitigation wall that survives even a real proxied Chromium render, not
just a JS requirement — set `config.browser.provider` to `"scraping_browser"`
to use Bright Data's Scraping Browser instead, a stronger, session-based
product. This is a different tool for a different failure, not a bigger
hammer for the same one: try `provider: "isp_proxy"` (the default) first, and
record why it still failed before reaching for `scraping_browser`. Neither
provider is for bypassing prohibiting terms. When Bright Data mistakenly restricts access to a public government or school-district page, `config.browser.provider` may instead be set to `"direct"` after recording the failed proxied attempt. This launches local Chromium inside
the same trusted runner.

Separately, when the fixed source page's real content only appears after a
click or a form/postback-driven control (an old-style listing that renders a
different result after selecting a value, not merely after JavaScript
finishes running), pass a bounded `actions` list to `browser_get()` —
`[{"click": "..."}, {"fill": ["...", "..."]}, {"press": "..."},
{"wait_for_selector": "..."}, {"wait_ms": n}]`, run in order before the page
is read. This is available with either provider. It is still a short,
declarative vocabulary the trusted runner executes, not a live page handle:
the scraper describes intent, never drives arbitrary browser automation.

When minutes are unavailable but the district publishes captioned YouTube
recordings, emit each individual recording URL with `kind="video"` and register with
`youtube_transcripts: {"enabled": true, "languages": ["en"]}`. Review the
transcript with available agendas or packets, cite the recording URL, and label
auto-generated-caption limitations; never call a transcript approved minutes
or emit a channel/playlist URL.

For an official directly downloadable recording outside YouTube, including a
canonical public Google Drive MP4 viewer link, register with bounded
`video_transcripts` settings described in `references/scrapers.md`. The trusted
parent downloads and transcodes the media and calls the configured speech-to-
text model; generated scraper code never receives FFmpeg, media storage, or
OpenRouter credentials. Treat the transcript as machine-generated and retain
the official recording URL as its source.

Research the board's normal meeting calendar and when it usually posts agendas.
Choose an agenda lead time that causes a run when the agenda is expected to be
available, and choose a follow-up delay based on when minutes, recordings, or
other post-meeting records normally appear. Prefer the default `nth_weekday`
meeting rule when the calendar is regular; use `custom_dates` when the board
publishes an irregular list of dates. Use the district's IANA timezone and a
local run time outside the meeting itself.

```json
{
  "timezone": "America/Los_Angeles",
  "run_time": "09:00",
  "meetings": {
    "mode": "nth_weekday",
    "ordinal": 2,
    "weekday": "wednesday",
    "months": [1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12]
  },
  "agenda_days_before": 5,
  "follow_up_days_after": 3
}
```

For an irregular published calendar, replace `meetings` with, for example,
`{"mode":"custom_dates","dates":["2026-10-01","2026-12-17"]}`. Include
all currently published future dates; the schedule becomes exhausted after its
last follow-up run until an agent registers the next calendar.

Register the tested script with its source-page config and schedule:

```sh
python3 scripts/tools/scraper_store.py register \
  --district <slug> --script /tmp/<slug>_scraper.py \
  --config /tmp/<slug>_sources.json --schedule /tmp/<slug>_schedule.json
```

Registration stores a versioned script in Postgres and schedules the next
agenda or post-meeting check. Generated scraper source does not belong in git.

## 5. Run the initial collection manually

The onboarding agent is the manual trigger:

```sh
python3 scripts/pipeline/execute.py --district <slug> --trigger manual
```

Do not weaken host permissions, change users, edit service configuration, or
relax sandbox boundaries to make the runner executable. If the trusted runner
is unreachable because the repository lives below a non-traversable directory,
use an identical temporary runtime copy in an already-accessible location when
safe, or stop and report the deployment-path blocker.

Require `ok: true`, at least one document seen, and the expected 12-month date
range. Confirm the run row, new-document count, and verified object metadata.
An incomplete archive is not, by itself, a publication blocker. Publish a
useful official corpus with a clear coverage note when some meetings, minutes,
attachments, or early-window records are absent. Keep the entity draft only
when no usable recurring official corpus can be collected, the available
records cannot support a useful page, or source identity/access is unresolved.

Canary failure recording after the successful run: register a deliberately
broken copy with `--disabled`, run it manually, and require a failed DB run with
the expected `failure_kind`. Immediately re-register the known-good script with
the board-aware schedule and run it once more successfully. Never leave the
canary version active.

Inspect both the canary and restored run without SQL:

```sh
python3 scripts/tools/scraper_store.py status --district <slug>
```



## 6. Write past reports, page content, and alerts

Read [references/overview.md](references/overview.md) for overview coverage.
Read [references/document-review.md](references/document-review.md), then review
every collected document in the trailing-year corpus before concluding which
alerts exist. Work chronologically from the oldest records forward, loading
manageable groups of related documents into context. Treat alert discovery as
one continuous investigation: carry themes and unresolved questions forward,
notice changes over time, and chase promising leads into later records or
linked official sources. Do not reduce review to an isolated per-document loop.
Review one copy of byte-identical duplicates and account for every other
document as reviewed, duplicate, or unreadable.

Use whichever safe representation makes the source understandable: embedded
text, an official HTML or accessibility view, rendered PDF pages, OCR, or direct
visual inspection of page images. Store the original official bytes unchanged.
Visual inspection may discover and support candidates. One collected official
source is sufficient; a second independent source is not required. Keep
unsupported inference in draft, but do not require exact extracted-text matches
when the official record is legible or its item description is sufficiently
specific. Do not dismiss a specific agenda item merely because minutes are
unavailable; describe it as proposed or under consideration.

Maintain a lightweight temporary coverage ledger so no document is skipped;
use separate evolving notes for themes, events, questions, and candidates. The
ledger is an audit of completeness, not the unit of analysis. Account for and
review every collected document before publishing a zero-alert feed. Report
reviewed, duplicate, and unreadable counts and describe material gaps. Missing
records outside the collected corpus are a limitation, not an automatic reason
to hide the usable page.

After reviewing the corpus, write one structured past report for each distinct
meeting, leadership-body meeting, school announcement, budget publication, or
other official report represented by the collected records. A report is the
human-readable occurrence, not an individual file: group an agenda, packet,
minutes, and presentation for the same meeting into one report and attach all
of their collected links. Do not create four reports for four representations
of one meeting.

Create a report for each future meeting that has an official posted agenda or
agenda packet. Use the scheduled meeting date as `reportDate`, attach the
agenda, and describe agenda items only as proposed, planned, scheduled, or up
for consideration. Reuse the same `reportKey` when later packets, minutes, or
recordings arrive so the meeting is updated instead of duplicated.

When an official current phone/device policy or AI/generative-AI policy was
found, create a distinct `policy_publication` report for each one and attach the
collected policy document. Use the official adoption or revision date when
available and null otherwise. State whether the policy is school-specific or
districtwide through `schoolScope`, summarize what the source actually says,
and record a clear coverage limitation when either policy could not be found
after checking the likely official repositories.

Use a stable district-scoped `reportKey` so a later onboarding or refresh can
update the same report rather than duplicate it. Store a concise factual
`summary`, even when no item clears the alert threshold. Use the controlled
report types and exact JSON shape in [references/storage.md](references/storage.md).
Assign `schoolScope` to every report and alert using exact official school
names affected by the action. When every school at one or more grade levels is
affected, use `all_elementary`, `all_middle`, or `all_high` as array values;
these may be combined for multi-level actions. Use `["all schools"]` for districtwide
relevance. If you are unsure which school this applies to, list all district schools likely to be affected,
and only fall back to `["all schools"]` when you are really unsure.

Create reports as drafts while reviewing, then publish the reconciled set:

```sh
python3 scripts/tools/past_report_store.py create --district <slug> /tmp/report.json
python3 scripts/tools/past_report_store.py list --district <slug>
python3 scripts/tools/past_report_store.py update --district <slug> \
  --report-key <report-key> /tmp/report.json
```

The report tool is the only allowed write interface for `past_reports`. Do not
query or modify the table directly. Every document link must point to an
official HTTP(S) source collected for this product district or to its existing
object-store `storageKey`; the tool validates that relationship. Do not publish a
report with no document links. Missing or ambiguous dates may remain null and
must not be guessed.

Write feed and overview JSON. For a new district, store them as drafts:

```sh
python3 scripts/tools/page_store.py put-page --district <slug> \
  --kind overview --status draft /tmp/overview.json
python3 scripts/tools/page_store.py put-page --district <slug> \
  --kind feed --status draft /tmp/feed.json
```

For an already-published district, keep the existing pages live during review.
Do not use `--status draft` on their current page rows; publish the verified
replacement artifacts at the end of the workflow.

The overview is required editorial work, not a scraper report. Its core sections
must explain funding, governance, spending or an explicit data gap, and at least
one current project with status, funding source, dated timeline, and next step.
Keep collection mechanics only in `method` or `limitations`; never substitute
document counts, corpus health, or repeat-run results for information about the entity.

Keep the feed subtitle short and count-based. Use the structured summary shape
in [references/storage.md](references/storage.md), not an editorial paragraph.

### What makes an alert worth flagging

Read [references/alerts.md](references/alerts.md). Apply its threshold before
writing any alert. Leave uncertain candidates out or keep them in draft.

### Alert categories

Use only the categories defined in [references/alerts.md](references/alerts.md).
Do not invent a new category during onboarding; leave the alert in draft and
flag the taxonomy gap for the owner.

For each published alert, cite a `sourceUrl` collected for this entity.
`page_store.py` confirms that relationship. Exact literal phrases, local source
files, and a second corroborating source are not required. Before publication,
require a complete detail record with distinct `summary` (what happened),
`whyItMatters`, `evidence`, and source metadata (`sourceUrl`, `sourceTitle`,
`sourceHost`, and `sourceType`). Never publish an alert that would leave one of
the What happened, Why it matters, Evidence, or Source sections blank. Follow
the exact JSON shape in [references/storage.md](references/storage.md).

```sh
python3 scripts/tools/page_store.py put-alerts --district <slug> /tmp/alerts.json
```

### Local votes (initial review only)

During the initial document review, also record the district's local votes
with `vote_store.py`: the next board election (from the town/city clerk or
the state election calendar) and any school-relevant ballot or town-meeting
item the corpus surfaces — overrides, debt exclusions, bonds, parcel taxes.
This is one-time research at onboarding, not a scraper responsibility — do
not register any repeated collection for it. Vote sources may be official
government pages outside the collected corpus (clerk calendars, certified
results); use the shapes and status rules in
[references/storage.md](references/storage.md), and mark `passed`/`failed`
only from such an official record. Skip silently only when no official
source exists.



## 7. Verify and publish

Before changing any status to `published`, spot check that the collected data
looks correct, that all collected documents were reviewed, and that the past
reports reconcile to the distinct meetings/publications in the review ledger.
Publish partial official coverage when it is useful and honestly scoped. Agenda items and
official meeting descriptions may support alerts; describe them as proposed,
planned, scheduled, or under consideration unless an official record establishes
the outcome. A missing linked attachment does not invalidate a sufficiently
specific official item description.

Publish with `page_store.py` only after those checks. Finish by reporting the
district slug, backfill range, documents seen/new, page and alert statuses,
past-report count/status, next agenda and follow-up schedule, limitations, and any source or
taxonomy gaps.

## Editorial rules

- Make each page stand alone; do not compare it with another school page.
- Describe rather than campaign.
- Badge a fact verified only when a stored document says it. Calculations and
external comparisons are context even when their inputs are verified.
- State what a source does not establish.
- Keep descriptions simple and digestible, where your audience is kids and parents who may not know much about how schools operate.
- A number alone is not the story: give its load-bearing context — the direction of change and the prior value ("$100,000, down from $170,000"), and one plain clause on what a referenced program/plan actually does. Add this context by replacing vaguer words, not by adding length.
- If an alert covers adopting a new policy or plan, say what the policy lines are and how they change existing rules, if known/determined.
- Keep alerts concise and readable, staying at a 5th grade reading level.
