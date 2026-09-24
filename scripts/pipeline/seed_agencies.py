#!/usr/bin/env python3
"""Seed the agency list for a state from NCES Common Core of Data.

    python3 scripts/pipeline/seed_agencies.py --state CA
    python3 scripts/pipeline/seed_agencies.py --state CA --dry-run

Source is the Urban Institute Education Data API, which republishes the NCES
CCD directory as JSON with no key required.

Not the California Department of Education's own file, which would be the
obvious choice: CDE's download sits behind a Radware bot challenge that rejects
this host, and does so for a real headless browser as well as for curl — it is
blocking the datacenter IP, not the user agent. NCES also has the advantage of
being national, so extending past California is a change of one argument.

Rows are upserted on the NCES LEA id and never delete: an agency that disappears
from a later CCD vintage has usually merged rather than ceased to exist, and its
meeting history stays meaningful.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from db import connect, t  # noqa: E402

API = "https://educationdata.urban.org/api/v1/school-districts/ccd/directory"

# CCD agency types. The label is stored alongside the code because the code
# alone is unreadable in a query six months from now.
AGENCY_TYPES = {
    1: "Regular local school district",
    2: "Component of a supervisory union",
    3: "Supervisory union administrative center",
    4: "Regional education service agency (county office)",
    5: "State-operated agency",
    6: "Federally-operated agency",
    7: "Charter school agency",
    8: "Other education agency",
    9: "Specialized public school district",
}

FIPS = {"CA": 6, "MA": 25, "TX": 48, "NY": 36, "FL": 12}


def fetch(state: str, year: int) -> list[dict]:
    fips = FIPS.get(state.upper())
    if not fips:
        sys.exit(f"no FIPS mapping for {state}; add one to FIPS in this file")
    url = f"{API}/{year}/?fips={fips}"
    rows: list[dict] = []
    while url:
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = json.load(response)
        rows.extend(payload["results"])
        url = payload.get("next")
        print(f"  fetched {len(rows)}...", flush=True)
    return rows


def clean_enrollment(value) -> int | None:
    # CCD uses negative sentinels for missing/suppressed rather than null.
    return value if isinstance(value, int) and value >= 0 else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="CA")
    ap.add_argument("--year", type=int, default=2022)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only-districts", action="store_true",
                    help="load only regular school districts (agency_type 1)")
    args = ap.parse_args()

    print(f"NCES CCD {args.year}, state {args.state}")
    rows = fetch(args.state, args.year)

    if args.only_districts:
        rows = [r for r in rows if r.get("agency_type") == 1]

    counts: dict[int, int] = {}
    for r in rows:
        counts[r.get("agency_type")] = counts.get(r.get("agency_type"), 0) + 1
    print(f"\n{len(rows)} agencies")
    for code, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {str(code):>4}  {AGENCY_TYPES.get(code, 'unmapped'):<46} {n:>5}")

    if args.dry_run:
        print("\ndry run — nothing written")
        return

    inserted = updated = 0
    with connect() as conn:
        with conn.cursor() as cur:
            for r in rows:
                leaid = str(r.get("leaid") or "").strip()
                name = (r.get("lea_name") or "").strip()
                if not leaid or not name:
                    continue
                atype = r.get("agency_type")
                cur.execute(
                    f"""insert into {t('agencies')}
                          (nces_leaid, name, state, county, agency_type,
                           agency_type_label, enrollment)
                        values (%s, %s, %s, %s, %s, %s, %s)
                        on conflict (nces_leaid) do update
                          set name = excluded.name,
                              county = excluded.county,
                              agency_type = excluded.agency_type,
                              agency_type_label = excluded.agency_type_label,
                              enrollment = excluded.enrollment,
                              updated_at = now()
                        returning (xmax = 0) as inserted""",
                    (leaid, name, args.state.upper(), r.get("county_name"), atype,
                     AGENCY_TYPES.get(atype), clean_enrollment(r.get("enrollment"))),
                )
                if cur.fetchone()[0]:
                    inserted += 1
                else:
                    updated += 1

            cur.execute(f"select status, count(*) from {t('agencies')} group by status")
            status_rows = cur.fetchall()

    print(f"\ninserted {inserted}, updated {updated}")
    print("status breakdown:")
    for status, n in status_rows:
        print(f"  {status:<20} {n:>5}")


if __name__ == "__main__":
    main()
