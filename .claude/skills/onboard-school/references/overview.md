# What belongs on the overview page

The overview answers "where does this place stand", as against the feed's "what
happened recently". It should still read sensibly cold, months later.

## keyStructure — the one thing to understand first

The first section carries whatever a reader must grasp before the rest of the
page means anything. It is deliberately named for its *role*, not for one
school's answer, because the answer differs:

- **A community-funded district** — the funding mechanism genuinely is the
thing. Property tax exceeding the state entitlement explains almost everything
downstream.
- **A school inside a large district** — governance, not funding. Who decides,
how far away they are, and whether they ever discuss this school.
- **An ordinary formula-funded district** — funding again, but frame it as the
common case rather than an exception.

Fields:


| Field          | Purpose                                       |
| -------------- | --------------------------------------------- |
| `navLabel`     | Short sidebar label — "Funding", "Governance" |
| `heading`      | The question the section answers              |
| `status`       | A badge: the one-line characterisation        |
| `explanation`  | A paragraph of prose                          |
| `points[]`     | 3–4 `{point, detail}` cards                   |
| `counterpoint` | Optional. The honest qualification            |


The `counterpoint` is worth using. A page that only prosecutes is less
trustworthy than one that says what the criticism does not establish — "none of
this means the school is badly run" is a stronger page, not a weaker one.

## Required coverage

Across `keyStructure`, `metrics` and `projects`, the page should let a parent
answer all four:

**1. How is it funded?** The mechanism, not just the amount. Which formula,
what drives it up or down, and what the school does or doesn't get automatically.

**2. How is it organised, and who decides?** The governing body, how its members
got there, what the school-level bodies are and what authority they actually
hold. Where a school has both a parent association and a statutory site council,
distinguish them — one is usually the feeder that elects members onto the other,
and conflating them is easy.

**3. What does it spend per student, and what shapes the budget?** Include
per-pupil, and be explicit about what it covers. A school-level allocation
typically excludes transport, benefits, facilities and central services, so it
can be roughly half of district revenue per pupil — say so, or the number
misleads. Add the school-specific budget determinants that a reader could not
guess: an equity-index quintile, an enrolment forecast the budget is built on, a
category that decides staffing ratios.

**4. What is underway, and when?** `projects[]` with dated `timeline` entries,
a `status`, funding source, and `nextMilestone`. A bond measure, a construction
programme, a curriculum adoption, a council's annual cycle. Include projects
that are stalled or unfunded — "unfunded, being fundraised" is a real status and
often the most useful one on the page.

## Metrics

Each metric is `{id, label, value, unit}` plus:

- `verified: true` plus `sourceUrl`/`sourceTitle` → directly reported by a
  collected official source and badged **verified**
- no direct collected source → badged **context**, for external or derived figures

Rules that keep the badge meaningful:

- **A calculation is context.** Per-pupil derived from allocation ÷ enrolment is
`context` even when both inputs are verified. Put the arithmetic in the note.
- **External comparisons are context**, with `peerSource` naming where they came
from and what year.
- **Only attach a peer when it measures the same thing.** District revenue per
pupil against school allocation per pupil implies a gap that is mostly
definitional. No comparison beats a misleading one.
- **Zero is a legitimate value.** "Board matters naming this school: 0 of 996"
and "Published minutes: 0 of 10" are among the most informative metrics
available — pair them with a peer that gives the reader the scale.

Prefer descriptive metrics over advocacy ones. Budget, enrolment, spending and
counts of published documents describe. Vote tallies on governance referenda
argue.

## Page labels for a school rather than a district

Set these in the overview JSON; they default to district wording so existing
entities are untouched:

- `pageTitle` — "School overview"
- `pageSubtitle` — one line on what the page answers
- `metricsHeading` — "Where the school stands", or "What is on the public
record" where the finding is mostly absence
