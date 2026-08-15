from langgraph.checkpoint.memory import InMemorySaver
from tenderos_backend.orchestration.graph import build_graph

# InMemorySaver here is a pure graph-shape test fixture, not a production
# checkpointer choice - the real runtime path (jobs/workflow.py) uses
# checkpoint.get_checkpointer()'s AsyncPostgresSaver. This test only checks
# that build_graph() wires a structurally valid graph, never invokes it.


def test_build_graph_compiles_with_expected_nodes():
    graph = build_graph(InMemorySaver())

    node_names = set(graph.get_graph().nodes.keys())
    assert node_names >= {
        "load_context",
        "supervisor",
        "requirement_extraction",
        "requirement_gate",
        "specialist_dispatch",
        "compliance_analysis",
        "compliance_gate",
        "talent_analysis",
        "resource_selection_gate",
        "commercial_analysis",
        "commercial_approval_gate",
        "specialist_join",
        "final_gate",
    }
