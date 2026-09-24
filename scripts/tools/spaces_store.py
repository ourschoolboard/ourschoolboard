#!/usr/bin/env python3
"""Upload source bytes to DigitalOcean Spaces through a narrow interface.

The caller supplies a file and provenance metadata. Credentials stay inside
this tool, keys are content-addressed, and every upload is verified with HEAD
before success is reported. The tool never deletes or overwrites an object.

    python3 scripts/tools/spaces_store.py put ./minutes.pdf \
      --source-url https://district.example/minutes.pdf --district weston-ma
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    candidates = []
    explicit_path = Path(os.environ["ENV_FILE"]) if os.environ.get("ENV_FILE") else None
    if explicit_path:
        if not explicit_path.is_file():
            raise RuntimeError(f"ENV_FILE={explicit_path} could not be read")
        candidates.append(explicit_path)
    candidates.append(ROOT / ".env")
    for path in candidates:
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split("#", 1)[0].strip().strip('"').strip("'")
            if (key.startswith("DO_SPACES_") and value
                    and (path == explicit_path or key not in os.environ)):
                os.environ[key] = value
        break


def spaces_client():
    load_env()
    needed = ("DO_SPACES_ENDPOINT", "DO_SPACES_KEY", "DO_SPACES_SECRET", "DO_SPACES_BUCKET")
    missing = [key for key in needed if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"missing object-storage configuration: {', '.join(missing)}")
    return boto3.client(
        "s3",
        endpoint_url=os.environ["DO_SPACES_ENDPOINT"],
        region_name=os.environ.get("DO_SPACES_REGION", "us-east-1"),
        aws_access_key_id=os.environ["DO_SPACES_KEY"],
        aws_secret_access_key=os.environ["DO_SPACES_SECRET"],
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sniff_content_type(path: Path) -> str:
    head = path.read_bytes()[:8]
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04") and path.suffix.lower() == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if head.lstrip().startswith(b"<"):
        return "text/html"
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def put_file(path: Path, *, district: str | None = None,
             source_url: str | None = None) -> dict:
    """Store one immutable object and return verified metadata."""
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = sha256_file(path)
    extension = path.suffix.lower() if len(path.suffix) <= 12 else ""
    key = f"sources/{digest[:2]}/{digest}{extension}"
    size = path.stat().st_size
    content_type = sniff_content_type(path)
    client = spaces_client()
    bucket = os.environ["DO_SPACES_BUCKET"]

    exists = False
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        exists = (
            head.get("ContentLength") == size
            and head.get("Metadata", {}).get("sha256") == digest
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "403"}:
            raise

    metadata = {"sha256": digest}
    if district:
        metadata["district"] = district[:120]
    if source_url:
        metadata["source-url-sha256"] = hashlib.sha256(source_url.encode()).hexdigest()
    if not exists:
        client.upload_file(
            str(path), bucket, key,
            ExtraArgs={"Metadata": metadata, "ContentType": content_type},
        )

    head = client.head_object(Bucket=bucket, Key=key)
    if head.get("ContentLength") != size or head.get("Metadata", {}).get("sha256") != digest:
        raise RuntimeError(f"object verification failed for {key}")
    return {
        "bucket": bucket,
        "key": key,
        "sha256": digest,
        "bytes": size,
        "contentType": content_type,
        "uploaded": not exists,
    }


def public_object_url(key: str) -> str:
    endpoint = urlparse(os.environ["DO_SPACES_ENDPOINT"])
    return f"{endpoint.scheme}://{os.environ['DO_SPACES_BUCKET']}.{endpoint.netloc}/{key}"


def put_public_image(path: Path, *, district: str, source_url: str) -> dict:
    """Store one immutable, browser-readable optimized page image."""
    path = path.resolve()
    if not path.is_file() or path.suffix.lower() != ".webp":
        raise ValueError("public page images must be WebP files")
    digest = sha256_file(path)
    key = f"images/{digest[:2]}/{digest}.webp"
    size = path.stat().st_size
    client = spaces_client()
    bucket = os.environ["DO_SPACES_BUCKET"]
    metadata = {
        "sha256": digest,
        "district": district[:120],
        "source-url-sha256": hashlib.sha256(source_url.encode()).hexdigest(),
    }
    head = None
    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "403"}:
            raise
    if head is None:
        client.upload_file(str(path), bucket, key, ExtraArgs={
            "ACL": "public-read",
            "CacheControl": "public, max-age=31536000, immutable",
            "ContentType": "image/webp",
            "Metadata": metadata,
        })
    head = client.head_object(Bucket=bucket, Key=key)
    if head.get("ContentLength") != size or head.get("Metadata", {}).get("sha256") != digest:
        raise RuntimeError(f"public image verification failed for {key}")
    return {"url": public_object_url(key), "storageKey": key, "sha256": digest, "bytes": size}


def get_file(key: str, destination: Path) -> dict:
    """Restore one content-addressed source and verify it locally."""
    if not key.startswith("sources/") or ".." in key or not key.rsplit("/", 1)[-1]:
        raise ValueError("only content-addressed sources/ keys may be read")
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    client = spaces_client()
    bucket = os.environ["DO_SPACES_BUCKET"]
    head = client.head_object(Bucket=bucket, Key=key)
    expected = head.get("Metadata", {}).get("sha256")
    if not expected:
        raise RuntimeError(f"object has no sha256 metadata: {key}")
    client.download_file(bucket, key, str(destination))
    actual = sha256_file(destination)
    if actual != expected or destination.stat().st_size != head.get("ContentLength"):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"download verification failed for {key}")
    return {"key": key, "file": str(destination), "sha256": actual,
            "bytes": destination.stat().st_size}


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    put = sub.add_parser("put")
    put.add_argument("file", type=Path)
    put.add_argument("--district")
    put.add_argument("--source-url")
    get = sub.add_parser("get")
    get.add_argument("--key", required=True)
    get.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = (put_file(args.file, district=args.district, source_url=args.source_url)
                  if args.command == "put" else get_file(args.key, args.out))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)
    print(json.dumps({"ok": True, **result}))


if __name__ == "__main__":
    main()
