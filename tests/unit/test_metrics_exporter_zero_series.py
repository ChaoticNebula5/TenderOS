"""Observability PR 32 (changes 1 and 2): the metrics_exporter emits every
known dead-letter event type and every known (action, rule) run ending at 0
before its first event, so the alerts built on a rise (DeadLetter) or on
increase() (RunsEndedBySystem) see the first one. A series that first
appears at 1 has no earlier sample: tenderos.rules.test.yml shows both
rules quiet on one.

The run-ending names come from domain/run_endings.py, which the reaper
itself imports, so the two lists cannot drift.
"""

from tenderos_backend.domain.events.outbox_events import ALL_EVENT_TYPES
from tenderos_backend.domain.run_endings import (
    ACTION_RUN_REAPED,
    ACTION_RUN_TIMED_OUT,
    KNOWN_RUN_ENDINGS,
    REAPER_RULES,
)
from tenderos_backend.domain.services import run_reaper
from tenderos_worker.metrics_exporter import PostgresState, postgres_metrics


def _empty_state(**overrides) -> PostgresState:
    values = {
        "runs": {},
        "run_oldest": {},
        "operations": {},
        "operation_oldest": {},
        "dead_letters": {},
        "reaped": {},
    }
    values.update(overrides)
    return PostgresState(**values)


def _samples(state: PostgresState, family_name: str) -> dict[tuple, float]:
    (family,) = [f for f in postgres_metrics(state, now=0.0) if f.name == family_name]
    return {tuple(sorted(s.labels.items())): s.value for s in family.samples}


def test_every_known_event_type_is_emitted_at_zero_before_its_first_dead_letter():
    samples = _samples(_empty_state(), "tenderos_outbox_dead_letters")
    assert samples == {(("event_type", t),): 0 for t in ALL_EVENT_TYPES}


def test_a_dead_letter_of_an_unknown_type_still_shows():
    samples = _samples(
        _empty_state(dead_letters={"WorkflowRunCreated": 2, "SomethingNew": 1}),
        "tenderos_outbox_dead_letters",
    )
    assert samples[(("event_type", "WorkflowRunCreated"),)] == 2
    assert samples[(("event_type", "SomethingNew"),)] == 1
    assert len(samples) == len(ALL_EVENT_TYPES) + 1


def test_every_known_run_ending_is_emitted_at_zero_before_its_first_event():
    samples = _samples(_empty_state(), "tenderos_workflow_runs_ended_by_system")
    assert samples == {(("action", a), ("rule", r)): 0 for a, r in KNOWN_RUN_ENDINGS}
    assert len(samples) == len(REAPER_RULES) + 1


def test_a_run_ending_with_a_rule_the_list_does_not_know_still_shows():
    samples = _samples(
        _empty_state(reaped={(ACTION_RUN_REAPED, "cap"): 3, (ACTION_RUN_REAPED, "brand_new"): 1}),
        "tenderos_workflow_runs_ended_by_system",
    )
    assert samples[(("action", ACTION_RUN_REAPED), ("rule", "cap"))] == 3
    assert samples[(("action", ACTION_RUN_REAPED), ("rule", "brand_new"))] == 1


def test_the_known_rules_are_the_reapers_own():
    """Every rule the reaper can record, and only those: its RQ verdicts and
    its two own rules, read from the reaper module itself."""
    reaper_rules = {
        value
        for name, value in vars(run_reaper).items()
        if (name.startswith("JOB_") or name.startswith("RULE_")) and isinstance(value, str)
    }
    assert set(REAPER_RULES) == reaper_rules
    assert (ACTION_RUN_TIMED_OUT, "") in KNOWN_RUN_ENDINGS
