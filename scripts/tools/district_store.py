#!/usr/bin/env python3
"""Create or update the identity record needed by a collector."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))
from db import connect, dict_cursor, t  # noqa: E402

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.file.read_text(encoding="utf-8"))
    slug = str(payload.get("slug") or "")
    name = str(payload.get("name") or "").strip()
    leaid = str(payload.get("agencyLeaid") or "").strip()
    if not SLUG.fullmatch(slug) or not name or not leaid:
        raise ValueError("slug, name, and agencyLeaid are required")
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(f"select id from {t('agencies')} where nces_leaid=%s", (leaid,))
        agency = cur.fetchone()
        if not agency:
            raise ValueError("agency must be seeded before creating its district")
        cur.execute(
            f"""insert into {t('districts')}
                  (slug,name,state,entity_type,nces_id,agency_id,governed_by,website,status,updated_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,'draft',now())
                on conflict (slug) do update set name=excluded.name,state=excluded.state,
                  entity_type=excluded.entity_type,nces_id=excluded.nces_id,
                  agency_id=excluded.agency_id,governed_by=excluded.governed_by,
                  website=excluded.website,updated_at=now()
                returning id,slug,name,status""",
            (slug, name, payload.get("state"), payload.get("entityType", "district"),
             payload.get("ncesId"), agency["id"], payload.get("governedBy"), payload.get("website")))
        print(json.dumps({"ok": True, "district": dict(cur.fetchone())}, default=str))


if __name__ == "__main__":
    main()
