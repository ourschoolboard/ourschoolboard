# OurSchoolBoard

Open-source components of [ourschoolboard.org](https://ourschoolboard.org):

- the public landing page;
- source-linked alert feeds and alert detail pages;
- the public journalist alert explorer; and
- the school-board collection, sandboxing, scheduling, transcription, and alert-review pipeline.

The onboarding skill also includes guarded storage interfaces for district
identity and pages, source-backed alerts, meeting reports, and local votes.

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

Static-page development works immediately after installing dependencies. Set
`DATABASE_URL` to run the data APIs and collection pipeline.

To exercise the guarded onboarding writers locally, set `APP_ENV=staging`, run
`npm run migrate`, and configure the S3-compatible object-store variables in
`.env`.

## Collection model

Generated collectors execute as unprivileged child processes with a narrow HTTP
capability. The trusted parent validates document candidates, downloads source
bytes, deduplicates them, stores provenance, and records every run.

See `.claude/skills/onboard-school/SKILL.md` for the supported onboarding workflow.

## License

GNU Affero General Public License v3.0. See [LICENSE](LICENSE).
