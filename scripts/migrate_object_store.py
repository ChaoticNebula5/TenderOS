"""Copy every object from one S3-compatible bucket to another, verifying each by SHA-256.

B17: written for the MinIO -> VersityGW swap (minio/minio can no longer be
pulled, see KNOWN_LIMITATIONS.md), but nothing here is vendor-specific - it
only uses list_objects_v2, get_object, put_object and head_bucket, through
boto3, against any two endpoints.

For each source object: read it, put it to the destination with its
ContentType and user metadata, read it back from the destination and compare
SHA-256. An object already at the destination with identical bytes is
skipped (still verified), so the copy can be re-run after an interruption.
Nothing is ever deleted from either side.

Credentials come only from the environment, never from argv (argv shows up
in process listings) and are never printed:
    SRC_S3_ACCESS_KEY, SRC_S3_SECRET_KEY, DST_S3_ACCESS_KEY, DST_S3_SECRET_KEY

Usage:
    uv run python scripts/migrate_object_store.py \
        --src-endpoint http://localhost:59200 --src-bucket tenderos \
        --dst-endpoint http://localhost:9000 --dst-bucket tenderos \
        [--prefix documents/] [--workers 8] [--dry-run] [--report out.json]

    # After a copy: check that every key in a file (one per line) exists at
    # the destination, e.g. the storage_key column of the documents table.
    uv run python scripts/migrate_object_store.py \
        --dst-endpoint http://localhost:9000 --dst-bucket tenderos --check-keys keys.txt

Exit status is 0 only if every object copied (or skipped) and verified, or,
with --check-keys, if every listed key exists.
"""

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


@dataclass
class Report:
    listed: int = 0
    copied: int = 0
    skipped_identical: int = 0
    verified: int = 0
    bytes_copied: int = 0
    bytes_total: int = 0
    content_type_mismatches: list[str] = field(default_factory=list)
    hash_mismatches: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def _client(endpoint: str, prefix: str, region: str) -> Any:
    access, secret = (
        os.environ.get(f"{prefix}_S3_ACCESS_KEY"),
        os.environ.get(f"{prefix}_S3_SECRET_KEY"),
    )
    if not access or not secret:
        sys.exit(
            f"refusing: {prefix}_S3_ACCESS_KEY and {prefix}_S3_SECRET_KEY must be set in the environment"
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name=region,
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 5, "mode": "standard"},
            max_pool_connections=32,
        ),
    )


def _keys(client: Any, bucket: str, prefix: str) -> Iterator[tuple[str, int]]:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            yield obj["Key"], obj["Size"]


def _sha256_of(client: Any, bucket: str, key: str) -> tuple[str, int, str | None]:
    response = client.get_object(Bucket=bucket, Key=key)
    data = response["Body"].read()
    return hashlib.sha256(data).hexdigest(), len(data), response.get("ContentType")


def _ensure_bucket(client: Any, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def _copy_one(src: Any, dst: Any, args: argparse.Namespace, key: str) -> tuple[str, str, int]:
    """Copy and verify one object. Returns (outcome, key-or-detail, size in bytes).

    Runs in a worker thread and touches no shared state; the main thread
    aggregates the outcomes into the Report.
    """
    try:
        response = src.get_object(Bucket=args.src_bucket, Key=key)
        data = response["Body"].read()
        want = hashlib.sha256(data).hexdigest()
        content_type = response.get("ContentType")

        try:
            have, _, have_type = _sha256_of(dst, args.dst_bucket, key)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 404:
                raise
            have, have_type = None, None
        if have == want and have_type == content_type:
            return "skipped_identical", key, len(data)
        if args.dry_run:
            return "would_copy", key, len(data)

        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        if response.get("Metadata"):
            extra["Metadata"] = response["Metadata"]
        dst.put_object(Bucket=args.dst_bucket, Key=key, Body=data, **extra)

        got, _, got_type = _sha256_of(dst, args.dst_bucket, key)
        if got != want:
            return "hash_mismatch", key, len(data)
        if got_type != content_type:
            return "content_type_mismatch", key, len(data)
        return "copied", key, len(data)
    except Exception as exc:  # noqa: BLE001 - recorded per key, the run continues
        return "failure", f"{key}: {type(exc).__name__}", 0


def migrate(args: argparse.Namespace) -> Report:
    src = _client(args.src_endpoint, "SRC", args.region)
    dst = _client(args.dst_endpoint, "DST", args.region)
    if not args.dry_run:
        _ensure_bucket(dst, args.dst_bucket)
    report = Report()
    keys = [key for key, _ in _keys(src, args.src_bucket, args.prefix)]
    report.listed = len(keys)
    # boto3 clients are thread-safe; each worker returns its outcome and only
    # this (main) thread updates the report.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = pool.map(lambda k: _copy_one(src, dst, args, k), keys)
        for done, (outcome, detail, size) in enumerate(results, 1):
            report.bytes_total += size
            if outcome == "copied":
                report.copied += 1
                report.verified += 1
                report.bytes_copied += size
            elif outcome == "skipped_identical":
                report.skipped_identical += 1
                report.verified += 1
            elif outcome == "hash_mismatch":
                report.copied += 1
                report.hash_mismatches.append(detail)
            elif outcome == "content_type_mismatch":
                report.copied += 1
                report.content_type_mismatches.append(detail)
            elif outcome == "failure":
                report.failures.append(detail)
            if done % 1000 == 0:
                print(f"  {done}/{len(keys)}", flush=True)
    return report


def check_keys(args: argparse.Namespace) -> int:
    dst = _client(args.dst_endpoint, "DST", args.region)
    wanted = [line.strip() for line in open(args.check_keys, encoding="utf-8") if line.strip()]
    present = {key for key, _ in _keys(dst, args.dst_bucket, "")}
    missing = [key for key in wanted if key not in present]
    print(
        json.dumps(
            {
                "keys_checked": len(wanted),
                "present": len(wanted) - len(missing),
                "missing": len(missing),
                "missing_keys": missing,
            },
            indent=1,
        )
    )
    return 1 if missing else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--src-endpoint")
    parser.add_argument("--src-bucket")
    parser.add_argument("--dst-endpoint", required=True)
    parser.add_argument("--dst-bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--prefix", default="")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", help="write the full report as JSON to this path")
    parser.add_argument(
        "--check-keys", help="only check that every key in this file exists at the destination"
    )
    args = parser.parse_args()

    if args.check_keys:
        return check_keys(args)
    if not (args.src_endpoint and args.src_bucket):
        parser.error("--src-endpoint and --src-bucket are required unless --check-keys is given")

    report = migrate(args)
    summary = asdict(report)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=1)
    shown = {
        k: (v if not isinstance(v, list) else f"{len(v)}: {v[:10]}") for k, v in summary.items()
    }
    print(json.dumps(shown, indent=1))
    ok = not (report.hash_mismatches or report.content_type_mismatches or report.failures)
    if args.dry_run:
        return 0
    return 0 if ok and report.verified == report.listed else 1


if __name__ == "__main__":
    sys.exit(main())
