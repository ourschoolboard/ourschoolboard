# Where to look

Ordered roughly by how much they reward the effort. Check
`references/compliance.md` before fetching from any of them.

## District and board records

The primary source when the board actually discusses the school.

- **Vendor agenda portals.** Legistar (`webapi.legistar.com/v1/<tenant>/` is an
open, unauthenticated JSON API and the best thing in this category — it
carries individual roll-call votes), Granicus, BoardDocs, Simbli. Coverage is
often incomplete; check the date distribution of what you get before
concluding a school is unmentioned.
- **The district's own meeting pages**, which sometimes host recaps the vendor
portal does not.
- **Public Google Drive viewer links.** Keep the canonical
  `/file/d/<ID>/view` URL. When it wraps a PDF, register the scraper with the
  narrowly scoped `google_drive_view_pdf` executor option described in
  `scrapers.md`; this stores the actual official PDF rather than Drive's
  token-churning HTML shell. Complete and record compliance checks for both
  `drive.google.com` and `googleusercontent.com` first. Never rewrite the URL
  to robots-disallowed `/uc` or `/open` forms.
- **Municipal public-notice systems.** Open-meeting law forces agendas to be
posted somewhere; a city clerk's notice feed is frequently cleaner and more
permissive than the district's own CMS. Watch for rolling windows — one such
feed exposed only ten current notices with no archive at all.



## School-level bodies

Where the district board never names the school, this is the only genuinely
school-specific governance record.

- **California**: School Site Council, Local School Leadership Council, ELAC.
Education Code § 35147 makes meetings open and requires a paper agenda posted
72 hours ahead — but **requires no minutes and no online posting**. Anything
online is a principal's discretionary act.
- **Massachusetts**: School Council under M.G.L. c. 71 § 59C. Parents are
elected and hold statutory parity with staff; the council reviews the budget
and develops the improvement plan. Note § 59C's open-meeting cross-reference
points at sections repealed in 2009.
- Look for these on the school's own site, often as PDFs or `.docx` on a CDN
separate from the (frequently bot-blocked) index page.



## Budget and financial data

Usually the richest verifiable material, and often the only school-specific
numbers that exist.

- **Per-school allocation tables.** Districts publish these district-wide, which
means you also get every peer school for free — invaluable for checking
whether a figure is actually unusual.
- **School plans** — SPSA (California), School/Quality Improvement Plan
(Massachusetts). Often parameterised by cost centre; a URL that requires login
in one form may be public in another.
- **Live spending dashboards**, where a district runs one. Current-year only, so
snapshot rather than link.
- **ProPublica Nonprofit Explorer** for the booster/friends nonprofit attached
to a school. Revenue, expenses and assets by year reveal whether private
fundraising is a meaningful share of the school's resources. Use the
`/api/v2/` path; see compliance notes.
- **IRS** directly (Publication 1828, the Business Master File) — public domain
and the authoritative source where ProPublica is a mirror.
- **Urban Institute Education Data API** (`educationdata.urban.org`) for
district-level finance and enrolment, useful for peer comparison.



## State and federal data

- **State education department profiles** — enrolment, demographics,
accountability. Prefer server-rendered HTML pages over PDFs; several states
disallow their `/pdf` path while leaving the same figures readable in HTML.
- **NCES** — Common Core of Data for public schools, **Private School Universe
Survey** for private ones. Presence in PSS is itself proof a school is
private.
- **State school directories** for CDS codes, type and status.



## Local news

Good for the story around a decision and for spotting things no document
announces — a principal change, a closure proposal, a bond campaign. Treat it
as a lead to a primary document rather than as a citable source: an alert should
cite the record, not the reporting about it.

## What to do when a host blocks you

Blocked index pages are the common case, and the documents themselves are often
on an open CDN. In order of preference:

1. Find the same document on a different host (a city notice feed, a CDN, a
  state mirror).
2. Use the Wayback Machine — the CDX API (`matchType=domain`) enumerates what
  was archived. Rate-limited; back off rather than hammering.
3. Use an operator-configured browser or proxy to read terms when normal access is blocked.
4. Use a real browser session for URL discovery, then fetch the documents directly.

Note that a residential proxy will not defeat TLS-fingerprint blocking, and
headless browsers are detected too. Route around rather than escalating.
