"""Run `npm audit` in each given app folder and fail on any high or critical
finding, apart from the dated exceptions below.

npm audit cannot skip one advisory: `--audit-level=high` fails on every high
finding, and one advisory is reported once for each package on its path (plan
item 110's picomatch advisory showed as picomatch, fdir, tinyglobby, vite,
vitest and more, all high). This reads `npm audit --json` and follows each high or
critical package's paths to the advisories on them: the package passes only
if every high or critical advisory on its paths is excepted. A package
reported high with no such advisory on its paths fails too, since then the
report says something this check cannot account for.

Each exception names one advisory on one package, for dev and build tooling
only, and has an expiry date:
- it excuses nothing in a folder where the advisory is also reported with
  dev dependencies left out (`npm audit --omit=dev`), so a production
  dependency reaching it fails;
- after its date the check fails, so the exception is re-checked rather than
  kept by default;
- it fails when its advisory no longer appears in any folder's report, so it
  is removed once nothing needs it, instead of staying as standing permission.

Exit codes:
  0  no high or critical finding beyond the live exceptions
  1  at least one problem: an advisory not excepted, an excepted advisory a
     production dependency reaches, an exception past its date, or one whose
     advisory is no longer reported
  2  the check could not run (npm not found, no audit report in its output,
     or a report this check cannot read). A check that cannot run fails, so it
     never reads as clean.

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


# None live. The one this check started with (GHSA-fc5q-39xc-vjpf on picomatch,
# plan item 110) was removed when npm audit stopped reporting the advisory: it
# was withdrawn on 2026-11-18, and the stale check below failed as designed.
# A new exception is added here with its own reason, re-check and date, and
# tests/unit/test_check_npm_audit.py's test_no_exception_is_live changes with it.
EXCEPTIONS: tuple[AuditException, ...] = ()


class AuditCouldNotRun(Exception):
    pass


def advisory_id(advisory: dict) -> str:
    """The advisory's GHSA id, the last part of its URL; npm's own numeric
    id when the URL has none."""
    last = str(advisory.get("url", "")).rstrip("/").rsplit("/", 1)[-1]
    return last if last.startswith("GHSA-") else f"npm-{advisory.get('source')}"


def _advisory_key(advisory: dict) -> tuple:
    # Every field that tells two advisories apart, so none is ever merged
    # into another (npm's numeric source id alone could repeat or be missing).
    return (
        advisory.get("source"),
        advisory_id(advisory),
        advisory.get("name"),
        advisory.get("severity"),
        advisory.get("title"),
    )


def parse_report(output: str) -> dict:
    """The report from npm audit's stdout; AuditCouldNotRun when there is
    none, or when it is not in the shape this check reads (npm also exits
    non-zero when it finds anything, so the exit code alone says nothing)."""
    try:
        report = json.loads(output)
    except json.JSONDecodeError as exc:
        raise AuditCouldNotRun(f"npm audit printed no JSON report: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("vulnerabilities"), dict):
        raise AuditCouldNotRun(f"npm audit printed no report: {str(report)[:300]}")
    for name, entry in report["vulnerabilities"].items():
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("severity"), str)
            or not isinstance(entry.get("via"), list)
        ):
            raise AuditCouldNotRun(f"npm audit's entry for {name!r} has no severity or via list")
        for via in entry["via"]:
            if isinstance(via, str):
                if via not in report["vulnerabilities"]:
                    raise AuditCouldNotRun(f"{name!r} is reported via {via!r}, which has no entry")
            elif not (
                isinstance(via, dict)
                and isinstance(via.get("name"), str)
                and isinstance(via.get("severity"), str)
            ):
                raise AuditCouldNotRun(f"an advisory on {name!r} has no package name or severity")
    return report


def advisories_on_paths(report: dict, package: str) -> list[dict]:
    """Every advisory on the package's paths: its own (objects in its `via`)
    and, through each package it names in `via`, theirs."""
    vulnerabilities = report["vulnerabilities"]
    found: dict[tuple, dict] = {}
    seen: set[str] = set()
    pending = [package]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for via in vulnerabilities[name]["via"]:
            if isinstance(via, dict):
                found.setdefault(_advisory_key(via), via)
            else:
                pending.append(via)
    return list(found.values())


def all_advisories(report: dict) -> list[dict]:
    found: dict[tuple, dict] = {}
    for entry in report["vulnerabilities"].values():
        for via in entry["via"]:
            if isinstance(via, dict):
                found.setdefault(_advisory_key(via), via)
    return list(found.values())


def run_audit(folder: Path, production_only: bool = False) -> dict:
    npm = shutil.which("npm")
    if npm is None:
        raise AuditCouldNotRun("npm not found")
    command = [npm, "audit", "--json", *(["--omit=dev"] if production_only else [])]
    try:
        result = subprocess.run(
            command,
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


def _exception_for(advisory: dict, exceptions: Iterable[AuditException]) -> AuditException | None:
    for exception in exceptions:
        if (
            advisory_id(advisory) == exception.advisory
            and advisory.get("name") == exception.package
        ):
            return exception
    return None


def check(
    reports: dict[str, dict],
    production: dict[str, dict],
    today: date,
    exceptions: Iterable[AuditException] = EXCEPTIONS,
) -> tuple[list[str], list[str]]:
    """(problems, notes) for each folder's full report and its report with
    dev dependencies left out. Any problem fails the check; notes are what
    the live exceptions excused."""
    exceptions = tuple(exceptions)
    problems: list[str] = []
    notes: list[str] = []
    reported: set[tuple[str, str]] = set()
    for folder, report in reports.items():
        in_production = {_advisory_key(a) for a in all_advisories(production[folder])}
        reported.update((advisory_id(a), str(a.get("name"))) for a in all_advisories(report))
        # (line, excused) -> the packages the advisory reaches it through.
        lines: dict[tuple[str, bool], list[str]] = {}
        for package, entry in sorted(report["vulnerabilities"].items()):
            if entry["severity"] not in FAILING_SEVERITIES:
                continue
            failing = [
                a
                for a in advisories_on_paths(report, package)
                if a.get("severity") in FAILING_SEVERITIES
            ]
            if not failing:
                problems.append(
                    f"{folder}: {package} is reported {entry['severity']}, but no high or "
                    f"critical advisory is on its paths; read `npm audit` there"
                )
                continue
            for advisory in failing:
                line = (
                    f"{folder}: {advisory['name']} {advisory['severity']} "
                    f"{advisory_id(advisory)} {advisory.get('title')} ({advisory.get('url')})"
                )
                exception = _exception_for(advisory, exceptions)
                excused = False
                if exception is None or today > exception.expires:
                    verdict = "not excepted"
                elif _advisory_key(advisory) in in_production:
                    verdict = (
                        "a production dependency reaches it (npm audit --omit=dev), and "
                        "the exception covers dev and build tooling only"
                    )
                else:
                    verdict = f"excepted until {exception.expires.isoformat()}"
                    excused = True
                lines.setdefault((f"{line}: {verdict}", excused), []).append(package)
        for (text, excused), packages in lines.items():
            (notes if excused else problems).append(f"{text} [via {', '.join(packages)}]")
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
        production = {
            folder: run_audit(REPO_ROOT / folder, production_only=True) for folder in args.folders
        }
    except AuditCouldNotRun as exc:
        print(f"npm audit: could not run: {exc}")
        return 2
    # EXCEPTIONS named here, not left to check()'s default, so it is read when
    # main runs (a default is bound once, when check is defined).
    problems, notes = check(reports, production, datetime.now(UTC).date(), EXCEPTIONS)
    for note in notes:
        print(note)
    for problem in problems:
        print(f"FAIL {problem}")
    if problems:
        return 1
    print(
        f"npm audit: no high or critical finding beyond the exceptions ({', '.join(args.folders)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
