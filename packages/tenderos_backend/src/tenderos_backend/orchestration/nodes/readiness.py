import logging
import uuid

from tenderos_backend.domain.services.readiness_service import evaluate_readiness
from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


async def readiness_generation_node(state: TenderState) -> dict:
    """B12's readiness_generation node - plain sequential, no Send(), no
    interrupt() (same shape as project_graph_node/proposal_drafting_node:
    R4's readiness_generation task type has no per-task_id workflow_tasks
    row, and Part C's gate_type CHECK has no value for it - it's the
    computation that feeds final_gate's own interrupt(), not a gate
    itself). Sits in the seam B11 deliberately left as a final_gate
    pass-through (see route_after_red_team/route_after_finding_gate's own
    B12-era retargeting in routing.py) - the third "review_router" branch
    R2's topology diagram names but B11's build list explicitly scoped out.

    No LLM call, matching "readiness is deterministic over current
    records... the LLM may summarize blockers but does not decide
    ready=true" (Build Guide, verbatim) - evaluate_readiness is pure
    Postgres/Neo4j-freshness reads, same posture project_graph_node's own
    docstring establishes for graph projection.

    degraded_stages is passed straight through from state, not re-derived -
    it's LangGraph checkpoint state, not a persisted column (see
    readiness_service.py's own comment on why blocker 8 takes it as a
    parameter rather than querying for it).

    CodeRabbit review: wrapped in try/except, matching proposal_drafting_
    node/project_graph_node/red_team_node's own pattern - previously an
    evaluate_readiness failure (e.g. the bounded retry in that function
    exhausting its attempts, or a Postgres error) would kill the whole run
    with status stuck at "generating_readiness" and final_gate never
    reached at all. On failure, readiness_report_id is deliberately left
    OUT of the returned dict - final_gate_node's own existing fail-closed
    read (`report = ... if state.get("readiness_report_id") else None`,
    then `ready: False` when report is None) already does the right thing
    with a missing report id; this node doesn't need to duplicate that
    logic, just avoid setting a report id that was never actually produced.
    """
    tender_id = uuid.UUID(state["tender_id"])
    workflow_run_id = uuid.UUID(state["workflow_run_id"])

    await sync_run_status(workflow_run_id, "generating_readiness")

    try:
        result = await evaluate_readiness(tender_id, degraded_stages=state.get("degraded_stages"))
    except Exception as exc:
        logger.exception("readiness evaluation failed for tender %s", tender_id)
        return {"degraded_stages": [f"readiness_generation:{type(exc).__name__}"]}

    return {"readiness_report_id": str(result.report_id)}
