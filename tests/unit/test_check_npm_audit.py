"""Plan item 110: scripts/check_npm_audit.py. Reports are built here in the
shape `npm audit --json` prints (auditReportVersion 2): an advisory is an
object in the `via` of the package it is reported on, and each package on its
path names that package instead.

No exception is live since item 110's close-out: the picomatch advisory it
excused (GHSA-fc5q-39xc-vjpf) was withdrawn, and the stale check failed as
designed. The exception rules are tested here with a made-up exception passed
to check(), and test_no_exception_is_live pins the script's own list, so a new
real exception goes through these tests too."""

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

# A made-up advisory on a made-up package, for the exception rules.
EXCEPTED = "GHSA-xmpl-xmpl-xmpl"
PACKAGE = "globmatch"
BEFORE_EXPIRY = date(2026, 11, 18)
EXPIRY = date(2026, 12, 18)
MADE_UP = (
    check_npm_audit.AuditException(
        advisory=EXCEPTED,
        package=PACKAGE,
        expires=EXPIRY,
        reason="A made-up reason.",
        recheck="A made-up re-check.",
    ),
)
# The advisory item 110 excused, withdrawn on 2026-11-18.
WITHDRAWN = "GHSA-fc5q-39xc-vjpf"


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
    """A report with each advisory on its package, and vite depending on the
    made-up package when it is there (the path shape picomatch's report had)."""
    vulnerabilities: dict[str, dict] = {}
    for advisory in advisories:
        entry = vulnerabilities.setdefault(
            advisory["name"],
            {"name": advisory["name"], "severity": advisory["severity"], "via": []},
        )
        entry["via"].append(advisory)
    if PACKAGE in vulnerabilities:
        vite = vulnerabilities.setdefault(
            "vite", {"name": "vite", "severity": "high", "isDirect": True, "via": []}
        )
        vite["via"].append(PACKAGE)
    return {"auditReportVersion": 2, "vulnerabilities": vulnerabilities, "metadata": {}}


def _excepted(source: int = 1247710) -> dict:
    return _advisory(PACKAGE, EXCEPTED, "high", source)


def _withdrawn() -> dict:
    return _advisory("picomatch", WITHDRAWN, "high", 1247710)


def _both_apps(report: dict) -> dict[str, dict]:
    return {"apps/web": report, "apps/landing": report}


def _check(
    reports: dict[str, dict],
    today: date,
    production: dict[str, dict] | None = None,
    exceptions: tuple = MADE_UP,
):
    """check() with the made-up exception, and each folder's production-only
    report clean unless given."""
    production = production or {folder: _report() for folder in reports}
    return check_npm_audit.check(reports, production, today, exceptions)


def test_no_exception_is_live():
    """Item 110's close-out removed the only one. A new exception is reviewed
    here: its id, package and date."""
    assert check_npm_audit.EXCEPTIONS == ()


def test_the_withdrawn_picomatch_advisory_fails_if_it_comes_back():
    """If GHSA-fc5q-39xc-vjpf is reported on picomatch again, the check fails
    on it; a fresh dated exception is then a new decision."""
    report = _both_apps(_report(_withdrawn()))

    problems, notes = _check(report, BEFORE_EXPIRY, exceptions=check_npm_audit.EXCEPTIONS)

    assert notes == []
    assert len(problems) == 2
    assert all(f"picomatch high {WITHDRAWN}" in p and "not excepted" in p for p in problems)


def test_an_excepted_advisory_and_its_path_pass_before_the_expiry():
    problems, notes = _check(_both_apps(_report(_excepted())), BEFORE_EXPIRY)

    assert problems == []
    assert len(notes) == 2
    assert all(f"excepted until 2026-12-18 [via {PACKAGE}, vite]" in note for note in notes)


def test_another_high_advisory_on_a_package_on_the_same_path_still_fails():
    report = _report(_excepted(), _advisory("vite", "GHSA-aaaa-bbbb-cccc", "high", 1))

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and "GHSA-aaaa-bbbb-cccc" in problems[0]


def test_another_advisory_on_the_excepted_package_itself_still_fails():
    report = _report(_excepted(), _advisory(PACKAGE, "GHSA-dddd-eeee-ffff", "high", 2))

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and "GHSA-dddd-eeee-ffff" in problems[0]


def test_the_excepted_id_on_another_package_still_fails():
    report = _report(_excepted(), _advisory("micromatch", EXCEPTED, "high", 3))

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and problems[0].startswith("apps/web: micromatch high")


def test_two_advisories_sharing_a_source_id_are_both_checked():
    """npm's numeric source id is not trusted to tell advisories apart: a
    second advisory on the excepted package with the excepted one's id number,
    listed after it, still fails instead of being merged into it."""
    report = _report(_excepted(source=7), _advisory(PACKAGE, "GHSA-aaaa-bbbb-cccc", "high", 7))

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert len(problems) == 1 and "GHSA-aaaa-bbbb-cccc" in problems[0]


def test_a_production_dependency_reaching_the_excepted_advisory_fails():
    """The exception covers dev and build tooling only: where the advisory is
    also reported with dev dependencies left out, it excuses nothing."""
    reports = _both_apps(_report(_excepted()))
    production = {"apps/web": _report(_excepted()), "apps/landing": _report()}

    problems, notes = _check(reports, BEFORE_EXPIRY, production)

    assert len(problems) == 1
    assert problems[0].startswith(f"apps/web: {PACKAGE} high")
    assert "a production dependency reaches it" in problems[0]
    assert len(notes) == 1 and notes[0].startswith(f"apps/landing: {PACKAGE}")


def test_a_high_package_with_no_high_advisory_on_its_paths_fails():
    """npm marks a package high, but nothing high is on its paths: the report
    says something the check cannot account for, so it fails rather than
    passing."""
    report = _report(_excepted(), _advisory("minimatch", "GHSA-g3hv-75wg-m249", "moderate", 4))
    report["vulnerabilities"]["typescript-estree"] = {
        "name": "typescript-estree",
        "severity": "high",
        "via": ["minimatch"],
    }

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert problems == [
        "apps/web: typescript-estree is reported high, but no high or critical advisory "
        "is on its paths; read `npm audit` there"
    ]


@pytest.mark.parametrize(("severity", "fails"), [("moderate", False), ("critical", True)])
def test_the_threshold_stays_at_high_and_critical(severity, fails):
    report = _report(_excepted(), _advisory("minimatch", "GHSA-g3hv-75wg-m249", severity, 4))

    problems, _ = _check({"apps/web": report}, BEFORE_EXPIRY)

    assert bool(problems) is fails


def test_the_exception_holds_on_its_last_day_and_fails_the_day_after():
    report = _both_apps(_report(_excepted()))

    assert _check(report, EXPIRY)[0] == []
    problems, notes = _check(report, date(2026, 12, 19))

    assert notes == []
    expired = [p for p in problems if "expired on 2026-12-18" in p]
    assert len(expired) == 1 and "Re-check:" in expired[0] and "Reason given:" in expired[0]
    # Past its date it no longer excuses the advisory, in either app.
    assert (
        sum(p.startswith((f"apps/web: {PACKAGE}", f"apps/landing: {PACKAGE}")) for p in problems)
        == 2
    )


def test_an_exception_no_longer_reported_anywhere_fails():
    """The failure that closed item 110, kept for the next exception."""
    problems, _ = _check(_both_apps(_report()), BEFORE_EXPIRY)

    assert problems == [
        f"{EXCEPTED} is no longer reported; remove the exception (scripts/check_npm_audit.py)"
    ]


@pytest.mark.parametrize("reported_in", ["apps/web", "apps/landing"])
def test_an_exception_still_reported_in_one_app_is_not_stale(reported_in):
    reports = {
        folder: _report(_excepted()) if folder == reported_in else _report()
        for folder in ("apps/web", "apps/landing")
    }

    assert _check(reports, BEFORE_EXPIRY)[0] == []


@pytest.mark.parametrize(
    "output",
    [
        "",
        "npm error code ENOLOCK",
        '{"error": {"code": "ENOAUDIT"}}',
        "[]",
        # an entry with no via list
        '{"vulnerabilities": {"vite": {"name": "vite", "severity": "high"}}}',
        # a path through a package with no entry
        '{"vulnerabilities": {"vite": {"name": "vite", "severity": "high", "via": ["fdir"]}}}',
        # an advisory with no severity
        '{"vulnerabilities": {"picomatch": {"name": "picomatch", "severity": "high",'
        ' "via": [{"name": "picomatch", "url": "u"}]}}}',
    ],
)
def test_output_this_check_cannot_read_is_a_check_that_could_not_run(output):
    with pytest.raises(check_npm_audit.AuditCouldNotRun):
        check_npm_audit.parse_report(output)


def test_main_exits_2_when_the_audit_cannot_run(monkeypatch, capsys):
    def no_report(folder, production_only=False):
        raise check_npm_audit.AuditCouldNotRun("no report")

    monkeypatch.setattr(check_npm_audit, "run_audit", no_report)

    assert check_npm_audit.main(["apps/web"]) == 2
    assert "could not run" in capsys.readouterr().out


def test_main_audits_without_dev_dependencies_too_and_exits_1_or_0(monkeypatch):
    full = {"report": _report(_excepted())}
    production = {"report": _report()}
    calls: list[bool] = []

    def audit(folder, production_only=False):
        calls.append(production_only)
        return (production if production_only else full)["report"]

    monkeypatch.setattr(check_npm_audit, "run_audit", audit)
    monkeypatch.setattr(check_npm_audit, "datetime", _FixedNow(BEFORE_EXPIRY))
    monkeypatch.setattr(check_npm_audit, "EXCEPTIONS", MADE_UP)
    assert check_npm_audit.main(["apps/web"]) == 0
    assert calls == [False, True]

    production["report"] = _report(_excepted())
    assert check_npm_audit.main(["apps/web"]) == 1


def test_main_as_shipped_passes_a_moderate_report_and_fails_a_high_one(monkeypatch):
    """With the list empty: a report with nothing high passes (both apps'
    report today), and any high advisory fails."""
    current = {"report": _report(_advisory("minimatch", "GHSA-g3hv-75wg-m249", "moderate", 4))}

    def audit(folder, production_only=False):
        return _report() if production_only else current["report"]

    monkeypatch.setattr(check_npm_audit, "run_audit", audit)
    monkeypatch.setattr(check_npm_audit, "datetime", _FixedNow(BEFORE_EXPIRY))
    assert check_npm_audit.main(["apps/web", "apps/landing"]) == 0

    current["report"] = _report(_withdrawn())
    assert check_npm_audit.main(["apps/web", "apps/landing"]) == 1


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


@pytest.mark.parametrize(
    ("production_only", "command"),
    [(False, ["npm", "audit", "--json"]), (True, ["npm", "audit", "--json", "--omit=dev"])],
)
def test_run_audit_leaves_dev_dependencies_out_only_when_asked(
    monkeypatch, production_only, command
):
    ran: list[list[str]] = []

    class _Done:
        stdout = '{"vulnerabilities": {}}'

    def fake_run(args, **kwargs):
        ran.append(args)
        return _Done()

    monkeypatch.setattr(check_npm_audit.shutil, "which", lambda name: name)
    monkeypatch.setattr(check_npm_audit.subprocess, "run", fake_run)

    assert check_npm_audit.run_audit(Path("."), production_only=production_only) == {
        "vulnerabilities": {}
    }
    assert ran == [command]
