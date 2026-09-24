# OurSchoolBoard

Open-source components of [ourschoolboard.org](https://ourschoolboard.org):

- the public landing page;
- source-linked alert feeds and alert detail pages;
- the public journalist alert explorer; and
- the school-board collection, sandboxing, scheduling, transcription, and alert-review pipeline.

This repository begins from a current-state snapshot. It contains no private
repository history, production data, district profile pages, internal notes,
deployment configuration, or Modal agent tooling.

## Run locally

Requirements: Node.js 20+, Python 3.11+, PostgreSQL, and FFmpeg when processing
recordings.

```sh
npm ci
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
cp .env.example .env
npm run migrate
npm test
npm run dev
```

The site is usable without a database for static-page development. Data APIs
return `503` until `DATABASE_URL` is configured.

## Collection model

Generated collectors execute as unprivileged child processes. They receive a
narrow HTTP capability and can emit document candidates, but never receive
database or object-storage credentials. The trusted parent validates candidates,
downloads source bytes, deduplicates them, stores provenance, and records every run.

See `.claude/skills/onboard-school/SKILL.md` for the supported onboarding workflow.

## Scope

The public app is intentionally read-only. It does not include subscriptions,
comments, reactions, alert chat, password-gated journalist tools, district
profile pages, production deployment configuration, or application data.

## License

Apache-2.0. See [LICENSE](LICENSE).
