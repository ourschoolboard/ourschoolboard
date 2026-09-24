#!/usr/bin/env python3
"""Database access for the scraping pipeline.

Deliberately the only module in scripts/pipeline/ that knows the connection
string exists. Generated scrapers run in a child process that never sees it —
they reach the database through a narrow, append-only capability instead. See
sandbox.py.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def table_prefix() -> str:
    """Mirror backend/env.js: staging gets its own copy of every table."""
    return "staging_" if os.environ.get("APP_ENV") == "staging" else ""


def t(name: str) -> str:
    return f"{table_prefix()}{name}"


def load_env() -> None:
    """Read an env file the way systemd does, not the way a shell would.

    ENV_FILE points at /etc/ourschoolboard/<env>.env in production. Those files
    contain values like `MAIL_FROM=name <addr>` which are valid for systemd but
    are a redirect under `. file` in bash, so they must never be shell-sourced.

    An explicitly selected ENV_FILE is authoritative for keys it defines. This
    prevents inherited credentials from a different environment from silently
    overriding a staging or production selection. The implicit repository .env
    keeps the conventional behavior where real environment variables win.
    """
    candidates = []
    explicit_path = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else None
    if explicit_path:
        if not explicit_path.is_file():
            sys.exit(f"ENV_FILE={explicit_path} could not be read")
        candidates.append(explicit_path)
    candidates.append(Path(__file__).resolve().parents[2] / ".env")

    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and (path == explicit_path or key not in os.environ):
                os.environ[key] = value
        break


@contextmanager
def connect():
    load_env()
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
