from langgraph.graph import END

from tenderos_backend.orchestration.state import TenderState


def route_after_requirement_gate(state: TenderState) -> str:
    if state["status"] == "rejected":
        return END
    return "specialist_dispatch"
