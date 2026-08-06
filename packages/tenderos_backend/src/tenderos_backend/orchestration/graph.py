from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tenderos_backend.orchestration.nodes.final_gate import final_gate_node
from tenderos_backend.orchestration.nodes.load_context import load_context_node
from tenderos_backend.orchestration.nodes.requirement import (
    commercial_stub_node,
    compliance_stub_node,
    requirement_gate_node,
    requirement_stub_node,
    talent_stub_node,
)
from tenderos_backend.orchestration.nodes.specialist_dispatch import dispatch_specialists
from tenderos_backend.orchestration.nodes.specialist_join import specialist_join_node
from tenderos_backend.orchestration.nodes.supervisor_node import supervisor_node
from tenderos_backend.orchestration.routing import route_after_requirement_gate
from tenderos_backend.orchestration.state import TenderState


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    builder = StateGraph(TenderState)

    builder.add_node("specialist_dispatch", lambda _state: {})
    builder.add_node("load_context", load_context_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("requirement_stub", requirement_stub_node)
    builder.add_node("requirement_gate", requirement_gate_node)
    builder.add_node("compliance_stub", compliance_stub_node)
    builder.add_node("talent_stub", talent_stub_node)
    builder.add_node("commercial_stub", commercial_stub_node)
    builder.add_node("specialist_join", specialist_join_node)
    builder.add_node("final_gate", final_gate_node)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "supervisor")
    builder.add_edge("supervisor", "requirement_stub")
    builder.add_edge("requirement_stub", "requirement_gate")
    builder.add_edge("compliance_stub", "specialist_join")
    builder.add_edge("talent_stub", "specialist_join")
    builder.add_edge("commercial_stub", "specialist_join")
    builder.add_edge("specialist_join", "final_gate")
    builder.add_edge("final_gate", END)

    builder.add_conditional_edges(
        "requirement_gate",
        route_after_requirement_gate,
        {
            "specialist_dispatch": "specialist_dispatch",
            END: END,
        },
    )
    builder.add_conditional_edges("specialist_dispatch", dispatch_specialists)
    return builder.compile(checkpointer=checkpointer)
