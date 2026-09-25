# Pre-built school-board CMS scrapers

Prefer a tested platform adapter over generated district-specific code. Detect
the meeting platform, confirm its configuration identifiers, and check the
platform's recorded terms before selecting an adapter.

## Available adapters

No production-ready adapters currently implement the onboarding pipeline's
`scrape(ctx)` contract.

| Platform | Adapter | Required configuration | Status |
| --- | --- | --- | --- |

Do not treat legacy district-specific fetchers as pre-built adapters. Only code
implementing the current sandboxed `scrape(ctx)` contract qualifies.

When adapters are added, list only tested implementations here. Likely platform
families include BoardDocs, CivicPlus AgendaCenter, Granicus/Legistar,
BoardBook, Novus, and Simbli/eBOARDsolutions. Inclusion in this list must not
override compliance: for example, a technically functional Simbli adapter must
still be skipped where recorded terms prohibit automated collection.
