from langgraph.graph import END

from tenderos_backend.orchestration.state import TenderState


def route_after_requirement_gate(state: TenderState) -> str:
    if state["status"] == "rejected":
        return END
    return "specialist_dispatch"


def route_after_compliance_gate(state: TenderState) -> str:
    # compliance_gate sits after specialist_join, before final_gate (see
    # compliance_gate_node's own docstring for why it's positioned there and
    # not nested inside compliance_analysis's Send()'d branch).
    if state["status"] == "rejected":
        return END
    return "final_gate"
