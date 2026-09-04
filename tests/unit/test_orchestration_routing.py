from langgraph.graph import END
from tenderos_backend.orchestration.routing import (
    route_after_commercial_approval_gate,
    route_after_compliance_gate,
    route_after_requirement_gate,
    route_after_resource_selection_gate,
)


def _state(**overrides) -> dict:
    base = {
        "status": "running_specialists",
        "dispatch_task_ids": ["task-a", "task-b", "task-c"],
    }
    return {**base, **overrides}


def test_route_after_requirement_gate_proceeds_to_dispatch_when_not_rejected():
    # cost_budget_gate now sits between requirement_gate and
    # specialist_dispatch (I5, Build Guide :2755-2763) - see
    # test_cost_budget_routing.py for that gate's own routing.
    assert route_after_requirement_gate(_state()) == "cost_budget_gate"


def test_route_after_requirement_gate_ends_on_rejection():
    assert route_after_requirement_gate(_state(status="rejected")) == END


def test_route_after_compliance_gate_proceeds_to_resource_selection_gate_when_not_rejected():
    assert route_after_compliance_gate(_state()) == "resource_selection_gate"


def test_route_after_compliance_gate_ends_on_rejection():
    assert route_after_compliance_gate(_state(status="rejected")) == END


def test_route_after_resource_selection_gate_proceeds_to_commercial_approval_gate_when_not_rejected():
    assert route_after_resource_selection_gate(_state()) == "commercial_approval_gate"


def test_route_after_resource_selection_gate_ends_on_rejection():
    assert route_after_resource_selection_gate(_state(status="rejected")) == END


def test_route_after_commercial_approval_gate_proceeds_to_proposal_drafting_when_not_rejected():
    assert route_after_commercial_approval_gate(_state()) == "proposal_drafting"


def test_route_after_commercial_approval_gate_ends_on_rejection():
    assert route_after_commercial_approval_gate(_state(status="rejected")) == END
