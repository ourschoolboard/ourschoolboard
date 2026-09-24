#!/usr/bin/env python3
"""Extract verification text from a restored official source document."""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree


class TextHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def normalize(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip()
             for line in text.replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line).strip() + "\n"


def extract(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        from pypdf import PdfReader
        return normalize("\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages))
    if head.startswith(b"PK\x03\x04"):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "word/document.xml" in names:
                xml_names = ["word/document.xml"]
            elif any(name.startswith("ppt/slides/slide") and name.endswith(".xml")
                     for name in names):
                xml_names = sorted(
                    name for name in names
                    if name.startswith("ppt/slides/slide") and name.endswith(".xml")
                )
            elif "xl/sharedStrings.xml" in names:
                xml_names = ["xl/sharedStrings.xml"]
            else:
                raise RuntimeError("unsupported ZIP or OOXML document")
            parts = []
            for name in xml_names:
                root = ElementTree.fromstring(archive.read(name))
                parts.extend(node.text or "" for node in root.iter()
                             if node.tag.endswith("}t"))
        return normalize(" ".join(parts))
    raw = path.read_text(encoding="utf-8", errors="replace")
    if "<" in raw and ">" in raw:
        parser = TextHTMLParser()
        parser.feed(raw)
        return normalize(html.unescape(" ".join(parser.parts)))
    return normalize(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.out.exists():
            raise FileExistsError(f"refusing to overwrite {args.out}")
        text = extract(args.file)
        if not text.strip():
            raise RuntimeError("source yielded no text; use OCR or a source-specific extractor")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    print(json.dumps({"ok": True, "file": str(args.out), "characters": len(text)}))


if __name__ == "__main__":
    main()
