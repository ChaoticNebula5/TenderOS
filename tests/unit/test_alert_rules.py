"""Observability PR 32: the alert rules' shape, checked without Docker.

CI runs promtool itself (the alert-rules job: check config, check rules,
test rules). This guard keeps what promtool does not require: every alert
has a severity and the three annotations an operator reads (what it means,
what to check first, and that its numbers are estimates), at least one
promtool case where it fires and one where it stays quiet, and request
ratios that leave out scrapes and healthchecks (PR 32 change 3).
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RULES_DIR = REPO_ROOT / "observability/prometheus/rules"
INTERNAL_EXCLUSION = 'route!~"/metrics|/health/.+"'


def _rules() -> dict[str, dict]:
    groups = yaml.safe_load((RULES_DIR / "tenderos.rules.yml").read_text(encoding="utf-8"))
    return {rule["alert"]: rule for group in groups["groups"] for rule in group["rules"]}


def _checks() -> list[dict]:
    tests = yaml.safe_load((RULES_DIR / "tenderos.rules.test.yml").read_text(encoding="utf-8"))
    return [check for case in tests["tests"] for check in case["alert_rule_test"]]


def test_the_test_file_loads_the_rule_file():
    tests = yaml.safe_load((RULES_DIR / "tenderos.rules.test.yml").read_text(encoding="utf-8"))
    assert tests["rule_files"] == ["tenderos.rules.yml"]


@pytest.mark.parametrize("name", sorted(_rules()))
def test_every_alert_has_a_severity_and_its_three_annotations(name: str):
    rule = _rules()[name]
    assert rule["labels"]["severity"] in {"critical", "warning"}
    assert set(rule["annotations"]) == {"summary", "first_check", "estimate"}
    # PR 32 answer F: each threshold says it is an estimate, and when to
    # revisit it.
    estimate = rule["annotations"]["estimate"]
    assert "estimate" in estimate
    assert "Gemini run" in estimate and "hosting" in estimate
    # promtool compares annotations exactly, so a summary built from $value
    # would pin floating-point output in the tests.
    assert "$value" not in str(rule["annotations"])


@pytest.mark.parametrize("name", sorted(_rules()))
def test_every_alert_has_a_firing_and_a_quiet_case(name: str):
    checks = [c for c in _checks() if c["alertname"] == name]
    assert any(c["exp_alerts"] for c in checks), f"{name}: no case where it fires"
    assert any(not c["exp_alerts"] for c in checks), f"{name}: no case where it stays quiet"


def test_every_tested_alert_exists():
    assert {c["alertname"] for c in _checks()} <= set(_rules())


def test_request_ratios_leave_out_scrapes_and_healthchecks():
    """Scrapes (every 15 s) and container healthchecks pad a ratio's
    denominator and hide errors at low traffic: every selector of the
    request counter in a rule carries the exclusion."""
    for name, rule in _rules().items():
        expr = rule["expr"]
        selectors = re.findall(r"tenderos_http_requests_total(\{[^}]*\})?", expr)
        for selector in selectors:
            assert INTERNAL_EXCLUSION in selector, f"{name}: {selector or 'no selector'}"


def test_no_rule_alerts_on_a_wait_at_a_gate_or_on_every_409():
    """A run at a gate is aged from its creation, so a normal wait looks old;
    the graph routes answer 409 GRAPH_STALE in normal use (pr32-plan.md)."""
    for name, rule in _rules().items():
        assert "waiting_for_human" not in rule["expr"], name
        assert 'code="409"' not in rule["expr"], name


def test_counts_that_can_fall_are_watched_as_a_rise_not_an_increase():
    """Dead letters and failed runs fall on a re-drive or a delete, and runs
    ended by the system and failed reanalysis claims fall when a tender or
    organization is deleted (their rows cascade); increase() would read a
    fall as a counter reset and count it."""
    rules = _rules()
    for name, gauge in (
        ("DeadLetter", "tenderos_outbox_dead_letters"),
        ("RunsFailing", "tenderos_workflow_runs"),
        ("OperationsFailing", "tenderos_operations"),
        ("RunsEndedBySystem", "tenderos_workflow_runs_ended_by_system_total"),
        # Plan item 124: a tender's reanalysis claims go with it.
        ("ReanalysisFailed", "tenderos_reanalysis_claims_failed"),
    ):
        expr = rules[name]["expr"]
        assert "min_over_time(" in expr and "[1h]" in expr, name
        assert "increase(" not in expr and "delta(" not in expr, name
        assert gauge in expr, name
