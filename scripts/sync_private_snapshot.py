#!/usr/bin/env python3
"""Import the explicitly public subset from a private source checkout.

Public-specific server, feed, journalist UI, baseline schema, and skill files
remain canonical here. This command only refreshes shared implementation files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "scripts" / "public_sync_manifest.json"

INDEX_REPLACEMENTS = {
    'href="/start">Find my school': 'href="/explore">Find my district',
    'href="/school-board-guide">What is a school board': 'href="#why">What is a school board',
    'href="/phone-policy">Explore the phone policy guide': 'href="/journalist-data">Explore alert patterns',
    'href="/about">About us</a>': 'href="#why">How it works</a>',
    'href="/school-board-guide">Why school boards matter</a>': 'href="/explore">Browse districts</a>',
}


def tracked_commit(source: Path) -> str:
    return subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()


def copy_file(source: Path, relative: str) -> None:
    origin = source / relative
    if not origin.is_file():
        raise FileNotFoundError(f"allowlisted source is missing: {relative}")
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origin, target)


def transform_index(source: Path) -> None:
    text = (source / "frontend" / "index.html").read_text(encoding="utf-8")
    for old, new in INDEX_REPLACEMENTS.items():
        if old not in text:
            raise RuntimeError(f"landing transform anchor changed: {old}")
        text = text.replace(old, new)
    text = text.replace('      <a href="/about#privacy">Privacy</a>\n', "")
    text = text.replace('      <a href="/status">Status</a>\n', "")
    text = text.replace(
        "Find state and national patterns, dive deep into individual districts, and track policy rollout nationwide.",
        "Compare published alert patterns by state, topic, district, and month, then open the source-linked alert.",
    )
    text = text.replace(
        '<a class="landing-journalists-primary" href="mailto:ben@fix.school">Contact us for full access</a>',
        '<a class="landing-journalists-primary" href="/journalist-data">Explore the public data</a>',
    )
    text = text.replace(
        '<a class="landing-journalists-secondary" href="/journalist-data">Try a limited preview</a>',
        '<a class="landing-journalists-secondary" href="/journalists">For journalists</a>',
    )
    (ROOT / "frontend" / "index.html").write_text(text, encoding="utf-8")


def public_review_import() -> None:
    path = ROOT / "scripts" / "pipeline" / "review_alerts.py"
    text = path.read_text(encoding="utf-8")
    private = "from page_store import put_reviewed_alerts  # noqa: E402"
    if private not in text:
        raise RuntimeError("review-alert storage import changed upstream")
    text = text.replace(private, "from alert_store import put_reviewed_alerts  # noqa: E402")
    text = text.replace("or cases where you are absolutely unsure what school is affected. \n",
                        "or cases where you are absolutely unsure what school is affected.\n")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="clean checkout of ourschoolboard/school-transparency")
    args = parser.parse_args()
    source = args.source.resolve()
    if not (source / ".git").exists() and not subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--git-dir"], capture_output=True
    ).returncode == 0:
        raise ValueError("source must be a Git checkout")
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
        raise ValueError("source checkout must be clean")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for relative in manifest["copy"]:
        copy_file(source, relative)
    transform_index(source)
    public_review_import()
    commit = tracked_commit(source)
    marker = ROOT / "OPEN_SOURCE_MANIFEST"
    lines = marker.read_text(encoding="utf-8").splitlines()
    lines[0] = f"# Snapshot source: ourschoolboard/school-transparency@{commit}"
    marker.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "sourceCommit": commit, "filesCopied": len(manifest["copy"]) + 1}))


if __name__ == "__main__":
    main()
