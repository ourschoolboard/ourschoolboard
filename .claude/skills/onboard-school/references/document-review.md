# Continuous corpus review

Alert discovery is document-first. Review the complete trailing-year corpus;
do not start with a few suspected alerts and retrieve only their sources. Read
from the oldest records toward the newest so decisions, reversals, recurring
issues, and emerging patterns remain visible.

## Continuous review

Load manageable chronological batches of related records into context rather
than processing each document as a sealed task. Within each batch, compare
agendas, minutes, packets, presentations, and attachments from the same meeting
or period. Carry an evolving set of notes across batches:

- active themes and recurring issues;
- events or projects and how their status changes;
- unresolved questions and expected follow-up records;
- plausible alert candidates and the evidence still needed;
- names, amounts, dates, policies, facilities, and programs worth tracking.

When a record surfaces a promising idea, follow it through later meetings and
linked official sources. Revise, merge, split, promote, or reject candidates as
the larger sequence becomes clear. Alert creation is one synthesis over the
whole period, not a repeated classification call for each file.

## Review ledger

Create a lightweight temporary JSON or JSONL ledger outside the repository.
Include every collected document in the requested period with:

- source URL, meeting date, title, kind, and stored SHA-256;
- the representation used: embedded text, official alternate, OCR, or images;
- review state: `reviewed`, `duplicate`, or `unreadable`;
- chronological batch or meeting group;
- extraction or viewing limitations.

Keep thematic notes and alert candidates outside this ledger. The ledger proves
coverage; it must not force the reasoning into 44 independent mini-reviews.

Build the ledger from the complete set emitted by the scraper, not from URLs
chosen during editorial research. If the available narrow tools cannot
enumerate or reconcile that collected set, report the missing read capability
and keep the pages in draft. Do not infer completeness from scrape counts alone.

Review one stored object once when multiple records have the same SHA-256, then
mark the others `duplicate`. Similar titles or URLs are not proof of duplicate
content.

Before publication, reconcile ledger totals to the collected corpus. A zero-
alert result is valid only when all readable unique documents were reviewed.
Never silently sample.

Enumerate the complete collected corpus through the narrow read interface; do
not infer it from run counts or use SQL:

```sh
python3 scripts/tools/page_store.py list-sources --district example-ca
```

The result is chronological and includes the current object key, hash, byte
size, and version count for each logical record. Use `get-source` below for
each record that needs retrieval.

## Viewing ladder

Use more than one representation when the first is empty, garbled, incomplete,
or hides important tables, charts, scans, or attachments:

1. Retrieve the original stored bytes through `page_store.py get-source` and
   `spaces_store.py get`, then run `source_text.py`.
2. Check for an official HTML, accessibility, text, attachment, or downloadable
   version linked by the same official source.
3. Render relevant PDF pages to images with an available local PDF renderer and
   inspect the images directly. Inspect tables, charts, diagrams, annotations,
   and scanned pages even when embedded text exists.
4. Run local OCR on scanned or image-only pages when available, preserving the
   OCR output as a temporary text file tied to the stored source.
5. Use a browser or image viewer when layout, layering, or an interactive
   official presentation cannot be understood from extraction alone.

Do not modify the stored original or substitute an unofficial copy as the
alert's source. Secondary reporting may identify what to inspect but cannot
replace official evidence.

## Alert decisions

Apply `alerts.md` across the complete chronological review. Record plausible
candidates as they emerge, then test them against the whole period before
deciding whether they clear the threshold. One candidate may draw on a sequence
of records, while one document may support multiple candidates or none. Each
published alert must still cite one exact stored source that directly supports
its headline and summary.

Visual review may establish where a candidate is and what additional evidence
is needed. Publish when one exact stored official source reasonably supports
the headline and summary. A second source and exact extracted-text phrase are
not required. OCR or direct visual reading may support a claim when the
official page is clearly legible. Agenda descriptions can support
proposal-stage alerts, and a missing attachment does not defeat a specific
official item description. Do not turn proposals into completed outcomes or
use uncertain OCR guesses and ambiguous chart interpretation as fact.

Mark a document `unreadable` only after exhausting reasonable representations.
Describe material unreadable documents in page limitations; do not treat them
as evidence that nothing happened.
