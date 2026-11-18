"""Plan item 110: scripts/check_npm_audit.py. Reports are built here in the
shape `npm audit --json` prints (auditReportVersion 2): an advisory is an
object in the `via` of the package it is reported on, and each package on its
path names that package instead. The script's one real exception
(GHSA-fc5q-39xc-vjpf on picomatch) is checked as written, so a change to its
date, id or package goes through these tests too."""

import importlib.util
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_npm_audit", REPO_ROOT / "scripts" / "check_npm_audit.py"
)
assert _spec is not None and _spec.loader is not None
check_npm_audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_npm_audit)

PICOMATCH = "GHSA-fc5q-39xc-vjpf"
BEFORE_EXPIRY = date(2026, 11, 18)
EXPIRY = date(2026, 12, 18)


def _advisory(package: str, ghsa: str, severity: str, source: int) -> dict:
    return {
        "source": source,
        "name": package,
        "dependency": package,
        "title": f"{package} advisory {ghsa}",
        "url": f"https://github.com/advisories/{ghsa}",
        "severity": severity,
        "range": "*",
    }


def _report(*advisories: dict) -> dict:
    """A report with each advisory on its package, and vite depending on
    picomatch (the path the real report shows) when picomatch is there."""
    vulnerabilities: dict[str, dict] = {}
    for advisory in advisories:
        entry = vulnerabilities.setdefault(
            advisory["name"],
            {"name": advisory["name"], "severity": advisory["severity"], "via": []},
        )
        entry["via"].append(advisory)
    if "picomatch" in vulnerabilities:
        vite = vulnerabilities.setdefault(
            "vite", {"name": "vite", "severity": "high", "isDirect": True, "via": []}
        )
        vite["via"].append("picomatch")
    return {"auditReportVersion": 2, "vulnerabilities": vulnerabilities, "metadata": {}}


def _picomatch(source: int = 1247710) -> dict:
    return _advisory("picomatch", PICOMATCH, "high", source)


def _both_apps(report: dict) -> dict[str, dict]:
    return {"apps/web": report, "apps/landing": report}


def test_the_exception_is_the_one_reviewed():
    (exception,) = check_npm_audit.EXCEPTIONS
    assert (exception.advisory, exception.package, exception.expires) == (
        PICOMATCH,
        "picomatch",
        EXPIRY,
    )
    assert exception.reason and exception.recheck


def test_the_picomatch_advisory_and_its_path_pass_before_the_expiry():
    problems, notes = check_npm_audit.check(_both_apps(_report(_picomatch())), BEFORE_EXPIRY)

    assert problems == []
    assert len(notes) == 2 and all("excepted until 2026-12-18" in note for note in notes)


def test_another_high_advisory_on_a_package_on_the_same_path_still_fails():
    report = _report(_picomatch(), _advisory("vite", "GHSA-aaaa-bbbb-cccc", "high", 1))

    problems, _ = check_npm_audit.check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and "GHSA-aaaa-bbbb-cccc" in problems[0]


def test_another_advisory_on_picomatch_itself_still_fails():
    report = _report(_picomatch(), _advisory("picomatch", "GHSA-dddd-eeee-ffff", "high", 2))

    problems, _ = check_npm_audit.check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and "GHSA-dddd-eeee-ffff" in problems[0]


def test_the_excepted_id_on_another_package_still_fails():
    report = _report(_picomatch(), _advisory("micromatch", PICOMATCH, "high", 3))

    problems, _ = check_npm_audit.check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and problems[0].startswith("apps/web: micromatch high")


@pytest.mark.parametrize(("severity", "fails"), [("moderate", False), ("critical", True)])
def test_the_threshold_stays_at_high_and_critical(severity, fails):
    report = _report(_picomatch(), _advisory("minimatch", "GHSA-g3hv-75wg-m249", severity, 4))

    problems, _ = check_npm_audit.check({"apps/web": report}, BEFORE_EXPIRY)

    assert bool(problems) is fails


def test_the_exception_holds_on_its_last_day_and_fails_the_day_after():
    report = _both_apps(_report(_picomatch()))

    assert check_npm_audit.check(report, EXPIRY)[0] == []
    problems, notes = check_npm_audit.check(report, date(2026, 12, 19))

    assert notes == []
    expired = [p for p in problems if "expired on 2026-12-18" in p]
    assert len(expired) == 1 and "Re-check:" in expired[0] and "Reason given:" in expired[0]
    # Past its date it no longer excuses the advisory, in either app.
    assert (
        sum(p.startswith(("apps/web: picomatch", "apps/landing: picomatch")) for p in problems) == 2
    )


def test_an_exception_no_longer_reported_anywhere_fails():
    problems, _ = check_npm_audit.check(_both_apps(_report()), BEFORE_EXPIRY)

    assert problems == [
        f"{PICOMATCH} is no longer reported; remove the exception (scripts/check_npm_audit.py)"
    ]


@pytest.mark.parametrize("reported_in", ["apps/web", "apps/landing"])
def test_an_exception_still_reported_in_one_app_is_not_stale(reported_in):
    reports = {
        folder: _report(_picomatch()) if folder == reported_in else _report()
        for folder in ("apps/web", "apps/landing")
    }

    assert check_npm_audit.check(reports, BEFORE_EXPIRY)[0] == []


@pytest.mark.parametrize(
    "output", ["", "npm error code ENOLOCK", '{"error": {"code": "ENOAUDIT"}}', "[]"]
)
def test_output_with_no_report_is_a_check_that_could_not_run(output):
    with pytest.raises(check_npm_audit.AuditCouldNotRun):
        check_npm_audit.parse_report(output)


def test_main_exits_2_when_the_audit_cannot_run(monkeypatch, capsys):
    def no_report(folder):
        raise check_npm_audit.AuditCouldNotRun("no report")

    monkeypatch.setattr(check_npm_audit, "run_audit", no_report)

    assert check_npm_audit.main(["apps/web"]) == 2
    assert "could not run" in capsys.readouterr().out


def test_main_exits_1_on_a_problem_and_0_without(monkeypatch):
    reports = {"apps/web": _report(_picomatch())}
    monkeypatch.setattr(check_npm_audit, "run_audit", lambda folder: reports["apps/web"])
    monkeypatch.setattr(check_npm_audit, "datetime", _FixedNow(BEFORE_EXPIRY))
    assert check_npm_audit.main(["apps/web"]) == 0

    reports["apps/web"] = _report(_picomatch(), _advisory("vite", "GHSA-aaaa-bbbb-cccc", "high", 1))
    assert check_npm_audit.main(["apps/web"]) == 1


class _FixedNow:
    """Stands in for the module's `datetime`, so main's today is fixed."""

    def __init__(self, today: date) -> None:
        self._today = today

    def now(self, tz=None):
        today = self._today

        class _Moment:
            def date(self) -> date:
                return today

        return _Moment()
