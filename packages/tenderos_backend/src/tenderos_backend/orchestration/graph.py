from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from tenderos_backend.orchestration.nodes.commercial import (
    commercial_analysis_node,
    commercial_approval_gate_node,
)
from tenderos_backend.orchestration.nodes.compliance import (
    compliance_analysis_node,
    compliance_gate_node,
)
from tenderos_backend.orchestration.nodes.final_gate import final_gate_node
from tenderos_backend.orchestration.nodes.graph_projection import project_graph_node
from tenderos_backend.orchestration.nodes.load_context import load_context_node
from tenderos_backend.orchestration.nodes.proposal import proposal_drafting_node
from tenderos_backend.orchestration.nodes.readiness import readiness_generation_node
from tenderos_backend.orchestration.nodes.redteam import (
    finding_gate_node,
    red_team_node,
    targeted_replan_node,
)
from tenderos_backend.orchestration.nodes.requirement import requirement_gate_node
from tenderos_backend.orchestration.nodes.requirement_extraction import (
    requirement_extraction_node,
)
from tenderos_backend.orchestration.nodes.specialist_dispatch import dispatch_specialists
from tenderos_backend.orchestration.nodes.specialist_join import specialist_join_node
from tenderos_backend.orchestration.nodes.supervisor_node import supervisor_node
from tenderos_backend.orchestration.nodes.talent import (
    resource_selection_gate_node,
    talent_analysis_node,
)
from tenderos_backend.orchestration.routing import (
    route_after_commercial_approval_gate,
    route_after_compliance_gate,
    route_after_finding_gate,
    route_after_red_team,
    route_after_requirement_gate,
    route_after_resource_selection_gate,
    route_after_specialist_join,
    route_after_targeted_replan,
)
from tenderos_backend.orchestration.state import TenderState


def build_graph(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    builder = StateGraph(TenderState)

    builder.add_node("specialist_dispatch", lambda _state: {})
    builder.add_node("load_context", load_context_node)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("requirement_extraction", requirement_extraction_node)
    builder.add_node("requirement_gate", requirement_gate_node)
    builder.add_node("compliance_analysis", compliance_analysis_node)
    builder.add_node("compliance_gate", compliance_gate_node)
    builder.add_node("talent_analysis", talent_analysis_node)
    builder.add_node("resource_selection_gate", resource_selection_gate_node)
    builder.add_node("commercial_analysis", commercial_analysis_node)
    builder.add_node("commercial_approval_gate", commercial_approval_gate_node)
    builder.add_node("specialist_join", specialist_join_node)
    builder.add_node("proposal_drafting", proposal_drafting_node)
    builder.add_node("project_graph", project_graph_node)
    builder.add_node("red_team", red_team_node)
    builder.add_node("targeted_replan", targeted_replan_node)
    builder.add_node("finding_gate", finding_gate_node)
    builder.add_node("readiness_generation", readiness_generation_node)
    builder.add_node("final_gate", final_gate_node)

    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "supervisor")
    builder.add_edge("supervisor", "requirement_extraction")
    builder.add_edge("requirement_extraction", "requirement_gate")
    # compliance_analysis/talent_analysis/commercial_analysis (all Send()-
    # dispatched) flow straight to specialist_join, same shape for all
    # three branches - the three post-join gates (compliance_gate,
    # resource_selection_gate, commercial_approval_gate) are NOT nested
    # inside this Send()'d branch (see compliance_gate_node's own docstring
    # for why: an interrupt()-bearing node upstream of a Send() fan-in join
    # replays on every later, unrelated resume in the same continuation,
    # which broke the graph live in B7). All three sit after the join
    # instead, chained sequentially in the same safe slot final_gate
    # already occupies.
    builder.add_edge("compliance_analysis", "specialist_join")
    builder.add_edge("talent_analysis", "specialist_join")
    builder.add_edge("commercial_analysis", "specialist_join")
    # specialist_join's downstream edge became conditional in B11
    # (route_after_specialist_join) - see that function's own comment. On
    # the first pass it still goes to compliance_gate, unchanged; on a
    # revision pass it skips straight to proposal_drafting.
    # proposal_drafting (B9) sits in the same non-Send()'d sequential slot
    # the three post-join gates already occupy, between commercial_approval_
    # gate and final_gate - the "pass-through seam" the B8 plan flagged in
    # advance. Unlike those three, it's not a gate (Part C's gate_type CHECK
    # constraint has no 'proposal_review' value, and R2's own topology
    # places proposal_subgraph before the finding/readiness gates, not
    # gated itself) - a plain node, same shape as specialist_join/final_gate.
    builder.add_edge("proposal_drafting", "project_graph")
    # project_graph (B10) is the next fixed step in R2's own topology
    # diagram (proposal_subgraph -> project_graph -> red_team), confirmed
    # independently by R4's task_type enum (graph_projection is a real task
    # type) and by WorkflowStatus already carrying "projecting_graph"
    # between "drafting" and "red_teaming" since B3.
    builder.add_edge("project_graph", "red_team")
    # B12: readiness_generation is the "readiness" branch R2's own topology
    # diagram names, filled in now - a plain unconditional edge into
    # final_gate, same "seam" pattern project_graph occupied before B10 and
    # proposal_drafting occupied before B9. Both routes that used to go
    # straight to final_gate (route_after_red_team's "nothing open" branch,
    # route_after_finding_gate's "approved" branch) now land here first -
    # see routing.py's own B12-era comments on both.
    builder.add_edge("readiness_generation", "final_gate")
    builder.add_edge("final_gate", END)

    builder.add_conditional_edges(
        "requirement_gate",
        route_after_requirement_gate,
        {
            "specialist_dispatch": "specialist_dispatch",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "compliance_gate",
        route_after_compliance_gate,
        {
            "resource_selection_gate": "resource_selection_gate",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "resource_selection_gate",
        route_after_resource_selection_gate,
        {
            "commercial_approval_gate": "commercial_approval_gate",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "commercial_approval_gate",
        route_after_commercial_approval_gate,
        {
            "proposal_drafting": "proposal_drafting",
            END: END,
        },
    )
    builder.add_conditional_edges(
        "specialist_join",
        route_after_specialist_join,
        {
            "compliance_gate": "compliance_gate",
            "proposal_drafting": "proposal_drafting",
        },
    )
    # B11's bounded revision loop. review_router (R2's diagram) is
    # route_after_red_team, not a node - see graph.py's own Q1 note and
    # routing.py's docstrings for the full bound argument. "readiness" (B12)
    # now has a real node - both terminal branches below land there instead
    # of final_gate directly, per routing.py's own B12-era retargeting.
    builder.add_conditional_edges(
        "red_team",
        route_after_red_team,
        {
            "targeted_replan": "targeted_replan",
            "finding_gate": "finding_gate",
            "readiness_generation": "readiness_generation",
        },
    )
    builder.add_conditional_edges(
        "targeted_replan",
        route_after_targeted_replan,
        {
            "specialist_dispatch": "specialist_dispatch",
            "proposal_drafting": "proposal_drafting",
            "finding_gate": "finding_gate",
        },
    )
    builder.add_conditional_edges(
        "finding_gate",
        route_after_finding_gate,
        {
            "readiness_generation": "readiness_generation",
            END: END,
        },
    )
    builder.add_conditional_edges("specialist_dispatch", dispatch_specialists)
    return builder.compile(checkpointer=checkpointer)
