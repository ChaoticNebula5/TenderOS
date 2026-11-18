"""Run `npm audit` in each given app folder and fail on any high or critical
advisory, apart from the dated exceptions below.

npm audit cannot skip one advisory: `--audit-level=high` fails on every high
finding, and one advisory is reported once for each package on its path (one
picomatch advisory shows as picomatch, fdir, tinyglobby, vite, vitest and
more, all high). This reads `npm audit --json` and checks the advisories
themselves. A package's severity is the highest of the advisories on its
paths, so "no high or critical advisory left once the exceptions are taken
out" is the same as "every high finding leads only to an excepted advisory".
Any other advisory, on any package, still fails.

Each exception names one advisory on one package and has an expiry date: after
it the check fails, so the exception is re-checked rather than kept by
default. An exception whose advisory no longer appears in any folder's report
fails too, so it is removed once nothing needs it, instead of staying as
standing permission.

Exit codes:
  0  no high or critical advisory beyond the live exceptions
  1  at least one problem: an advisory not excepted, an exception past its
     date, or an exception whose advisory is no longer reported
  2  the check could not run (npm not found, or no audit report in its
     output). A check that cannot run fails, so it never reads as clean.

Run: uv run python scripts/check_npm_audit.py apps/web apps/landing

Standard library only.
"""

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOLDERS = ("apps/web", "apps/landing")
# The level CI has always failed at (it ran `npm audit --audit-level=high`).
FAILING_SEVERITIES = frozenset({"high", "critical"})
# Configured: a stuck audit fails the step (exit 2) instead of hanging the job.
TIMEOUT_SECONDS = 180


@dataclass(frozen=True)
class AuditException:
    advisory: str  # the GHSA id, as in the advisory's URL
    package: str  # the package the advisory is reported on
    expires: date  # the last day it excuses the advisory
    reason: str
    recheck: str


EXCEPTIONS: tuple[AuditException, ...] = (
    # Added 2026-11-18, plan item 110: remove when picomatch ships a fix.
    AuditException(
        advisory="GHSA-fc5q-39xc-vjpf",
        package="picomatch",
        expires=date(2026, 12, 18),
        reason=(
            "ReDoS in picomatch through 4.0.7, with no patched release. Reached "
            "only through dev and build tooling (vite, vitest, typescript-eslint): "
            "neither app's production dependencies include it, and neither app's "
            "build output contains it. To be affected, an attacker has to supply "
            "the glob pattern."
        ),
        recheck=(
            "Run `npm view picomatch version` and read the advisory. If a fixed "
            "picomatch is out, move to it and remove this exception; if not, and "
            "the reason still holds, set a new date."
        ),
    ),
)


class AuditCouldNotRun(Exception):
    pass


def advisory_id(advisory: dict) -> str:
    """The advisory's GHSA id, the last part of its URL; npm's own numeric
    id when the URL has none."""
    last = str(advisory.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
    return last if last.startswith("GHSA-") else f"npm-{advisory.get('source')}"


def advisories(report: dict) -> list[dict]:
    """Every advisory in an `npm audit --json` report, once each. An advisory
    is listed (as an object) in the `via` of the package it is reported on;
    the packages on its path list that package's name instead."""
    found: dict[object, dict] = {}
    for vulnerability in report["vulnerabilities"].values():
        for via in vulnerability.get("via", []):
            if isinstance(via, dict):
                found.setdefault(via.get("source"), via)
    return list(found.values())


def parse_report(output: str) -> dict:
    """The report from npm audit's stdout; AuditCouldNotRun when there is
    none (npm also exits non-zero when it finds anything, so the exit code
    alone says nothing)."""
    try:
        report = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AuditCouldNotRun(f"npm audit printed no JSON report: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("vulnerabilities"), dict):
        raise AuditCouldNotRun(f"npm audit printed no report: {str(report)[:300]}")
    return report


def run_audit(folder: Path) -> dict:
    npm = shutil.which("npm")
    if npm is None:
        raise AuditCouldNotRun("npm not found")
    try:
        result = subprocess.run(
            [npm, "audit", "--json"],
            cwd=folder,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuditCouldNotRun(f"npm audit took over {TIMEOUT_SECONDS} s in {folder}") from exc
    return parse_report(result.stdout)


def _excepted_by(
    advisory: dict, today: date, exceptions: Iterable[AuditException]
) -> AuditException | None:
    for exception in exceptions:
        if (
            advisory_id(advisory) == exception.advisory
            and advisory.get("name") == exception.package
            and today <= exception.expires
        ):
            return exception
    return None


def check(
    reports: dict[str, dict],
    today: date,
    exceptions: Iterable[AuditException] = EXCEPTIONS,
) -> tuple[list[str], list[str]]:
    """(problems, notes) for one report per folder. Any problem fails the
    check; notes are what the live exceptions excused."""
    exceptions = tuple(exceptions)
    problems: list[str] = []
    notes: list[str] = []
    reported: set[tuple[str, str]] = set()
    for folder, report in reports.items():
        for advisory in advisories(report):
            reported.add((advisory_id(advisory), str(advisory.get("name"))))
            if advisory.get("severity") not in FAILING_SEVERITIES:
                continue
            line = (
                f"{folder}: {advisory.get('name')} {advisory.get('severity')} "
                f"{advisory_id(advisory)} {advisory.get('title')} ({advisory.get('url')})"
            )
            excepted = _excepted_by(advisory, today, exceptions)
            if excepted is None:
                problems.append(line)
            else:
                notes.append(f"{line}: excepted until {excepted.expires.isoformat()}")
    for exception in exceptions:
        if today > exception.expires:
            problems.append(
                f"the exception for {exception.advisory} ({exception.package}) expired on "
                f"{exception.expires.isoformat()}. Reason given: {exception.reason} "
                f"Re-check: {exception.recheck}"
            )
        if (exception.advisory, exception.package) not in reported:
            problems.append(
                f"{exception.advisory} is no longer reported; remove the exception "
                f"(scripts/check_npm_audit.py)"
            )
    return problems, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folders", nargs="*", default=list(DEFAULT_FOLDERS))
    args = parser.parse_args(argv)
    try:
        reports = {folder: run_audit(REPO_ROOT / folder) for folder in args.folders}
    except AuditCouldNotRun as exc:
        print(f"npm audit: could not run: {exc}")
        return 2
    problems, notes = check(reports, datetime.now(UTC).date())
    for note in notes:
        print(note)
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(
        f"npm audit: no high or critical advisory beyond the exceptions ({', '.join(args.folders)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
