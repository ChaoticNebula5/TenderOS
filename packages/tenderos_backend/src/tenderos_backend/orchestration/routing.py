from langgraph.graph import END

from tenderos_backend.orchestration.state import TenderState


def route_after_requirement_gate(state: TenderState) -> str:
    if state["status"] == "rejected":
        return END
    return "specialist_dispatch"


def route_after_compliance_gate(state: TenderState) -> str:
    # compliance_gate sits after specialist_join, before resource_selection_
    # gate (see compliance_gate_node's own docstring for why it's positioned
    # there and not nested inside compliance_analysis's Send()'d branch).
    if state["status"] == "rejected":
        return END
    return "resource_selection_gate"


def route_after_resource_selection_gate(state: TenderState) -> str:
    # Same sequential-slot reasoning as compliance_gate above.
    if state["status"] == "rejected":
        return END
    return "commercial_approval_gate"


def route_after_commercial_approval_gate(state: TenderState) -> str:
    # Same sequential-slot reasoning as the two gates above - the last of
    # the three post-join gates before final_gate.
    if state["status"] == "rejected":
        return END
    return "final_gate"
