"""orchestration/routing.py::route_after_cost_budget_gate - I5's gate (Build
Guide :2755-2763). Plain synchronous state-reader, same shape as every other
route_after_* function - no DB, no LLM, pure unit tests against hand-built
TenderState dicts.
"""

from langgraph.graph import END
from tenderos_backend.orchestration.routing import route_after_cost_budget_gate


def _state(**overrides) -> dict:
    base = {"status": "running_specialists"}
    return {**base, **overrides}


def test_halts_on_rejection():
    assert route_after_cost_budget_gate(_state(status="rejected")) == END


def test_halts_on_changes_requested():
    assert route_after_cost_budget_gate(_state(status="changes_requested")) == END


def test_first_pass_goes_to_specialist_dispatch():
    # No revision_dispatch_target at all - the first-pass shape, reached
    # via route_after_requirement_gate.
    assert route_after_cost_budget_gate(_state()) == "specialist_dispatch"


def test_specialist_bound_revision_goes_to_specialist_dispatch():
    assert (
        route_after_cost_budget_gate(_state(revision_dispatch_target="specialist_dispatch"))
        == "specialist_dispatch"
    )


def test_proposal_drafting_bound_revision_goes_to_proposal_drafting():
    assert (
        route_after_cost_budget_gate(_state(revision_dispatch_target="proposal_drafting"))
        == "proposal_drafting"
    )
