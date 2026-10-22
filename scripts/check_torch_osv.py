"""Query OSV for advisories against the torch version(s) locked in uv.lock.

pip-audit skips torch: the lock takes it from the PyTorch CPU index, and a
version such as `2.13.0+cpu` is not on PyPI, so pip-audit reports "could not
be audited" and CI passes without ever checking it (KNOWN_LIMITATIONS, the
torch entry). This script closes that gap. It reads every torch version in
uv.lock, drops the local suffix (`+cpu`), and asks OSV, the database
pip-audit itself uses, for advisories against that PyPI version.

Exit codes:
  0  no advisory for any locked torch version
  1  at least one advisory (each is printed with its id and summary)
  2  the check could not run: no torch in the lock, or OSV unreachable or
     answering something unexpected. A check that cannot run fails, so a
     network problem never reads as "no advisories".

Run: uv run python scripts/check_torch_osv.py
     uv run python scripts/check_torch_osv.py --version 2.5.1   (a control:
     an old version with known advisories, to show the query works)

Standard library only, so it runs before or without `uv sync`.
"""

import argparse
import json
import sys
import tomllib
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OSV_QUERY_URL = "https://api.osv.dev/v1/query"
# Configured: a slow OSV answer fails the step (exit 2) instead of hanging the job.
TIMEOUT_SECONDS = 30


def locked_torch_versions(lock_path: Path) -> list[str]:
    """Every distinct torch version in the lock, local suffix removed. The
    lock has one entry per platform (2.13.0 for macOS, 2.13.0+cpu
    elsewhere); they share a base version today, but nothing guarantees it."""
    lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    versions = {
        package["version"].split("+", 1)[0]
        for package in lock.get("package", [])
        if package.get("name") == "torch"
    }
    return sorted(versions)


def osv_advisories(version: str) -> list[dict]:
    """All OSV vulnerabilities for torch==version on PyPI, following
    next_page_token until OSV says there are no more."""
    found: list[dict] = []
    page_token = None
    while True:
        body: dict = {"package": {"name": "torch", "ecosystem": "PyPI"}, "version": version}
        if page_token:
            body["page_token"] = page_token
        request = urllib.request.Request(
            OSV_QUERY_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"unexpected OSV answer: {type(payload).__name__}")
        found.extend(payload.get("vulns", []))
        page_token = payload.get("next_page_token")
        if not page_token:
            return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version",
        action="append",
        help="check this torch version instead of the lock's (repeatable)",
    )
    args = parser.parse_args()

    if args.version:
        versions = args.version
        source = "--version"
    else:
        try:
            versions = locked_torch_versions(REPO_ROOT / "uv.lock")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"ERROR: could not read uv.lock: {exc}", file=sys.stderr)
            return 2
        source = "uv.lock"
        if not versions:
            print("ERROR: no torch package in uv.lock; nothing was checked", file=sys.stderr)
            return 2

    any_advisory = False
    for version in versions:
        try:
            advisories = osv_advisories(version)
        # OSError covers URLError, TimeoutError and a dropped connection;
        # ValueError covers a malformed answer. Both must be exit 2, never
        # the traceback's exit 1, which would read as "advisories found".
        except (OSError, ValueError) as exc:
            print(f"ERROR: OSV query for torch {version} failed: {exc}", file=sys.stderr)
            return 2
        print(f"torch {version} ({source}): {len(advisories)} advisories in OSV")
        for advisory in advisories:
            any_advisory = True
            summary = advisory.get("summary") or advisory.get("details", "")[:100]
            print(f"  {advisory.get('id')}: {summary}")
    return 1 if any_advisory else 0


if __name__ == "__main__":
    sys.exit(main())
