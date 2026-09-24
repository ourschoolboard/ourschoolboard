---
name: onboard-school
description: Add or update a public school district collector using official sources, compliance checks, source-backed alert review, and board-aware scheduling.
---

# Onboard a school district collector

Build a repeatable, auditable collection path for public governing records. This
skill does not publish district profile pages, subscriptions, comments, or other
private product surfaces.

## Safety boundary

- Work in staging unless the operator explicitly authorizes production.
- Fetch public records only after checking the governing site and document hosts.
- Treat terms as the collection gate; record robots.txt as evidence, not as the
  sole legal conclusion.
- Never bypass authentication, access controls, or terms that prohibit collection.
- Store credentials only in environment variables consumed by trusted tools.
- Generated collectors may fetch through `ctx.get()` and emit through
  `ctx.emit_meeting()`; they never receive database or object-storage credentials.

## Workflow

1. Identify the exact public entity, stable NCES LEA identifier, official site,
   board page, meeting platform, and durable archive pages.
2. Run `scripts/pipeline/compliance.py` for every source host. Stop when binding
   terms prohibit automated collection.
3. Inspect agendas, packets, minutes, budgets, policies, and official recordings.
   Prefer stable official APIs and archives over search-result URLs.
4. Start from `assets/scraper_template.py`. Reuse a platform adapter when one is
   available; otherwise implement the smallest source-specific `scrape(ctx)`.
5. Cover the trailing twelve months plus already-posted future agendas. A source
   returning zero records is an error unless the source proves the archive is empty.
6. Register with `scripts/tools/scraper_store.py`. Use a board-calendar schedule
   when meeting dates are known; otherwise use a conservative interval cadence.
7. Run in staging twice. The first run must collect the expected records; the
   second must be clean and deduplicated. Exercise a deliberate failure canary
   before restoring the valid collector.
8. Review newly collected official records with
   `scripts/pipeline/review_alerts.py`. Published alerts must resolve to a stored
   source and accurately describe what the source establishes and does not establish.
9. Report source coverage, exclusions, run identifiers, document counts, alert
   counts, next scheduled run, and any remaining limitation.

## Relevant commands

```sh
python3 scripts/pipeline/seed_agencies.py --state <state> --dry-run
python3 scripts/tools/district_store.py <district.json>
python3 scripts/pipeline/compliance.py --leaid <id> --url <official-url>
python3 scripts/tools/scraper_store.py register --district <slug> \
  --script <collector.py> --config <config.json> --schedule <schedule.json>
python3 scripts/pipeline/execute.py --district <slug> --trigger manual
python3 scripts/pipeline/review_alerts.py --run-id <run-id>
```

Never claim completion from scraper exit status alone. Read the stored run back,
verify document provenance, and confirm the repeat run produced zero duplicates.
