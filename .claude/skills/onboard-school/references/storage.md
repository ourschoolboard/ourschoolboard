# Onboarding storage interfaces

Use these tools instead of SQL, SDK calls, committed JSON, or direct credential
access. Every command prints one JSON result and exits nonzero on failure.

## District identity

`page_store.py put-district` accepts:

```json
{
  "slug": "example-ca",
  "name": "Example Unified School District",
  "state": "CA",
  "entityType": "district",
  "ncesId": "0600001",
  "agencyLeaid": "0600001",
  "agencyName": "Example Unified School District",
  "agencyWebsite": "https://www.example.k12.ca.us/",
  "governedBy": "Example Unified Board of Education",
  "schoolBoardMembersList": [
    {
      "name": "Alex Rivera",
      "role": "Board President",
      "trusteeArea": "Area 2",
      "email": "alex.rivera@example.org",
      "termEnds": "2028",
      "profileUrl": "https://official.example/board/alex-rivera",
      "sourceUrl": "https://official.example/board"
    }
  ],
  "schoolBoardContactInfo": {
    "emails": ["board@example.org"],
    "phone": "555-0100",
    "mailingAddress": "1 School Way, Example, CA 90000",
    "contactUrl": "https://official.example/board/contact",
    "sourceUrl": "https://official.example/board"
  },
  "website": "https://www.example.k12.ca.us/",
  "status": "draft",
  "sourceEvidence": [
    {"url": "https://official.example/directory", "supports": "identity and governance"}
  ],
  "metadata": {}
}
```

`entityType` is `district`, `school`, or `charter`. For a school, `ncesId` is
the school's stable id and `agencyLeaid` is the governing district's LEA id.
The tool creates a missing governing agency only from the supplied LEA id and
agency identity; `agencyName` is required for a school's new agency. Follow
with `compliance.py` before scraper registration.

The snake-case aliases `school_board_members_list` and
`school_board_contact_info` are also accepted. Board members require `name` and
may include `role`, `trusteeArea`, `email`, `phone`, `termEnds`, `profileUrl`,
and `sourceUrl`. Contact information may include `emails`, `phone`,
`mailingAddress`, `contactUrl`, and `sourceUrl`. Use only current, explicitly
public official contact information; do not infer missing emails. Omitting
either field on a later district update preserves the stored value. Supplying
an empty array or object deliberately clears it.

Member entries may also carry `seatType` (`ward`, `at-large`, `ex-officio`, or
`appointed`), `firstElected` (a year), and `background` — at most two factual
sentences taken from an official bio, never inferred.

## Local votes

`vote_store.py put` upserts by a stable district-scoped `voteKey`; `list`
reads them back. One row per distinct vote: a board election, or a
school-relevant ballot/town-meeting item. For regional districts, write one
row per member town (`jurisdiction`).

```sh
python3 scripts/tools/vote_store.py put --district example-ca /tmp/votes.json
python3 scripts/tools/vote_store.py list --district example-ca
```

```json
{
  "voteKey": "2026-06-02-override-yarmouth",
  "kind": "override",
  "venue": "ballot",
  "title": "Proposition 2 1/2 override for the FY27 school budget",
  "summary": "Passing funds the adopted budget; failing forces cuts.",
  "amount": 1481348,
  "jurisdiction": "Yarmouth",
  "voteDate": "2026-06-02",
  "status": "scheduled",
  "detail": {"seatsUp": []},
  "sourceUrl": "https://official.example/clerk/elections",
  "lastVerified": "2026-08-19"
}
```

`kind` is one of `board_election`, `appointment`, `override`,
`debt_exclusion`, `bond`, `parcel_tax`, `charter_question`, `other`; `venue` is `ballot`,
`town_meeting`, `city_council` (a council appropriation roll call), or `appointing_body`. `status` follows the vote's life: `draft`, `proposed`,
`qualified`, `scheduled`, `passed`, `failed`, `withdrawn`. `qualified` and
`scheduled` require `voteDate`; `passed`/`failed` require a `sourceUrl`
citing an official record (a clerk's certified results page qualifies even
when outside the collected corpus). For a `board_election`, put the seats and
incumbents in `detail.seatsUp`.

When a board has no election because its seats are appointed, write one
`appointment` row (venue `appointing_body`, status `proposed` as a standing
arrangement, no date) whose summary states plainly that there is no public
election and names who appoints, citing the governing agreement or charter.
A specific dated appointment with an official record may instead use
`scheduled` or `passed`.

Board directory data remains attached to the district identity record as
`schoolBoardMembersList` and `schoolBoardContactInfo` for downstream consumers.

States are `draft`, `published`, and `paused`. Re-run `put-district` with the
full descriptor to change state.

## Pages

Store current page-shaped JSON with:

```sh
python3 scripts/tools/page_store.py put-page --district example-ca \
  --kind overview --status draft /tmp/overview.json
```

Kinds are `overview` and `feed`; states are `draft` and `published`. A write
replaces the current content for that district and kind atomically.

Keep alert rows out of the feed JSON. A published feed requires `district`, an
ISO-date `period`, a structured count-based `summary`, `categories`, and
`limitations`; it may also include `budget`, `timeline`, and `context`.
`/api/feed` attaches published alert rows. Keep the summary compact by using
counts, for example `{ "documentsRead": 45, "alertsPublished": 5 }`, rather
than writing a paragraph. Include every canonical category from `alerts.md`,
even when its current count is zero.

## Alerts

`put-alerts` accepts one alert, an array, or `{ "alerts": [...] }`. Each alert
requires `id`, `title`, and `status` (`draft`, `published`, or `withdrawn`). A
published alert must provide every field rendered by the alert detail page; the
tool rejects incomplete records instead of allowing blank sections.

```json
{
  "id": "example-2026-08-budget",
  "title": "Board adopts staffing reductions",
  "summary": "The board adopted 12 position reductions for 2026-27.",
  "whyItMatters": "The reductions change staffing available to students next year.",
  "evidence": "Approved minutes record the motion, number of positions, and vote.",
  "date": "2026-08-01",
  "topic": "budget",
  "categoryLabel": "Financial",
  "severity": "medium",
  "schoolScope": ["Example Middle School"],
  "sourceUrl": "https://official.example/meeting.pdf",
  "sourceTitle": "August 1, 2026 approved minutes",
  "sourceHost": "official.example",
  "sourceType": "Board meeting minutes",
  "status": "draft"
}
```

`summary`, `whyItMatters`, `evidence`, `categoryLabel`, `schoolScope`, `sourceTitle`,
`sourceHost`, and `sourceType` must be non-empty for publication. `sourceHost`
must match the hostname in `sourceUrl`. The source URL must resolve to a
document collected for this product district. No second source, literal phrase,
or local verification-file pair is required.
A source may be shared by several school pages through `district_documents`
without duplicating bytes.

## Meeting and official reports

Past-report records populate the Meetings & reports tab, which includes posted
upcoming agendas and also retains
historical meeting summaries and official publications. They are deliberately
broader than board meetings: one row represents one distinct official meeting,
announcement, or publication, and may cite several collected documents. A
future `reportDate` is valid when an official agenda or agenda packet has been
posted for that scheduled meeting.

Write them only through `past_report_store.py`. The tool accepts camelCase or
snake_case field aliases, but examples and API responses use camelCase:

```json
{
  "reportKey": "2026-06-18-regular-board-meeting",
  "reportDate": "2026-06-18",
  "reportType": "board_meeting",
  "title": "June 18 regular board meeting",
  "summary": "The board reviewed the proposed budget and approved the new mathematics materials.",
  "schoolScope": ["all schools"],
  "documentLinks": [
    {
      "title": "Meeting agenda",
      "url": "https://official.example/meetings/2026-06-18-agenda.pdf",
      "sourceType": "Agenda"
    },
    {
      "title": "Approved minutes",
      "storageKey": "sources/ab/abcdef.pdf",
      "sourceType": "Minutes",
      "contentType": "application/pdf"
    }
  ],
  "status": "draft"
}
```

Allowed `reportType` values are:

- `board_meeting`
- `local_leadership_meeting`
- `local_school_announcement`
- `budget_publication`
- `policy_publication`
- `plan_publication`
- `audit_publication`
- `other_official_report`

`reportKey`, `reportType`, `title`, `summary`, `schoolScope`, `documentLinks`, and `status` are
required. `reportDate` is an ISO date or null when the official source does not
provide a reliable date. Each document link needs a title and either a
collected official `url` or an existing object-store `storageKey`. A published report
must have at least one validated document link. Status is `draft` or
`published`.

For a future meeting, use the scheduled meeting date, attach the posted agenda
or packet, and keep the summary in proposal-stage language. Keep its
`reportKey` stable when later minutes or recordings are added. For a current
phone/device or AI/generative-AI policy, use `policy_publication`; use the
official adoption or revision date when available and null rather than an
inferred date.

For alerts and reports, `schoolScope` contains exact official school names that
the action affects or that a bounded review identifies as possibly affected.
The sentinel values `all_elementary`, `all_middle`, and `all_high` mean every
school in that NCES-derived level and may be combined, for example
`["all_middle", "all_high"]`. A school can belong to multiple levels when its
NCES grade span crosses a boundary.
Use the exact sentinel `["all schools"]` when the action is structurally
districtwide, such as the operating budget or tax levy, superintendent hiring,
the district calendar, or a universal policy. Never use it merely because the
board governs every school or because campus assignments are missing.

For staffing, program, or service changes without specific schools in the primary
source, check collected personnel records and current official staff
directories, course catalogs, and school plans. A best-supported likely scope
may be published when the alert text mentions the named schools are
"possibly affected" and why. If no credible school-level inference is possible,
but you know a change, for example, affects high schools, list all the high schools in the district.
Only when you're completely unsure, use `["all schools"]`. Never mix it with
individual school names. Existing rows may
return `[]` until their scope is reviewed; new published rows may not.

Create, inspect, and update rows without direct database access:

```sh
python3 scripts/tools/past_report_store.py create --district example-ca /tmp/report.json
python3 scripts/tools/past_report_store.py list --district example-ca
python3 scripts/tools/past_report_store.py get --district example-ca \
  --report-key 2026-06-18-regular-board-meeting
python3 scripts/tools/past_report_store.py update --district example-ca \
  --report-key 2026-06-18-regular-board-meeting /tmp/report.json
```

The public API returns only published reports belonging to a published district.

Look up and restore a source without SQL or SDK access:

```sh
python3 scripts/tools/page_store.py list-sources --district example-ca
python3 scripts/tools/page_store.py get-source --district example-ca \
  --url https://official.example/meeting.pdf
python3 scripts/tools/spaces_store.py get --key sources/ab/abcdef.pdf \
  --out /tmp/example-source.pdf
python3 scripts/tools/source_text.py /tmp/example-source.pdf \
  --out /tmp/example-source.txt
```

When repairing an existing collector, inspect the active authored source and
configuration through the narrow read-only command rather than querying the
database:

```sh
python3 scripts/tools/scraper_store.py get-active --district example-ca
```

## Source objects

`spaces_store.py put` accepts one local source file plus optional district slug
and source URL. It computes SHA-256, stores at
`sources/<sha-prefix>/<sha><extension>`, and verifies size and hash metadata.
It never deletes or overwrites an object.

Normal scraper ingestion calls this interface from the trusted executor. Use it
directly only for a manually obtained official source that cannot be emitted by
the registered scraper, then ensure its document metadata is created through an
approved ingestion path rather than SQL.

## Scrapers and schedules

`scraper_store.py register` validates the `scrape(ctx)` interface, rejects DB,
object-storage, socket, and subprocess imports, refuses an agency blocked by
recorded terms, versions the source, activates it, and upserts one schedule for
the district. It cannot modify page content or alerts. Pass `--schedule` with a
board-calendar JSON file to run once near expected agenda publication and once
after each meeting. The schedule accepts a recurring `nth_weekday` meeting rule
or a `custom_dates` calendar; `status` returns the stored schedule and
its next computed run. The legacy `--cadence-days` interval remains available
for existing and non-board collectors.

`execute.py` is the trusted parent. It is the only component that combines run
records, document metadata, version history, and object-store writes. The sandboxed
scraper receives none of those credentials.

Read schedule state and the last ten runs through the narrow interface:

```sh
python3 scripts/tools/scraper_store.py status --district example-ca
```
