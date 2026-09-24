#!/usr/bin/env python3
"""Backfill searchable extracted text for previously collected documents."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse

from db import connect, dict_cursor, load_env, t
from review_alerts import extract_bounded, store_document_text
from spaces_store import get_file


def missing_documents(district_slug: str | None, limit: int | None) -> list[dict]:
    conditions = ["x.document_version_id is null"]
    values: list[object] = []
    if district_slug:
        conditions.append("d.slug=%s")
        values.append(district_slug)
    limit_sql = ""
    if limit is not None:
        limit_sql = " limit %s"
        values.append(limit)
    with connect() as conn, dict_cursor(conn) as cur:
        cur.execute(
            f"""select m.id,m.source_url,v.id as document_version_id,
                       v.storage_key
                  from {t('agency_meetings')} m
                  join {t('districts')} d on d.id=m.district_id
                  join lateral (
                    select id,storage_key
                      from {t('agency_document_versions')}
                     where meeting_id=m.id order by fetched_at desc limit 1
                  ) v on true
             left join {t('agency_document_texts')} x on x.document_version_id=v.id
                 where {' and '.join(conditions)}
                 order by m.id{limit_sql}""",
            tuple(values),
        )
        return [dict(row) for row in cur.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--district")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    load_env()
    if os.environ.get("APP_ENV") not in {"staging", "production"}:
        raise RuntimeError("index_document_text.py requires APP_ENV=staging or production")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    documents = missing_documents(args.district, args.limit)
    indexed = 0
    unreadable = 0
    with tempfile.TemporaryDirectory(prefix="osb-document-index-") as temp:
        directory = Path(temp)
        for document in documents:
            suffix = Path(urlparse(document["source_url"]).path).suffix[:12] or ".bin"
            raw = directory / f"{document['document_version_id']}{suffix}"
            get_file(document["storage_key"], raw)
            try:
                document["text"] = extract_bounded(raw)
                store_document_text(document)
                indexed += 1
            except Exception:
                unreadable += 1
            finally:
                raw.unlink(missing_ok=True)
    print(json.dumps({"ok": unreadable == 0, "documentsFound": len(documents),
                      "indexed": indexed, "unreadable": unreadable}))


if __name__ == "__main__":
    main()
