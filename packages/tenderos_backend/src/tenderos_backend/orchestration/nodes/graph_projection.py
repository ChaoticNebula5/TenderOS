import logging
import uuid

from tenderos_backend.domain.services.graph_service import project_tender
from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


async def project_graph_node(state: TenderState) -> dict:
    """R2's project_graph step, reduced to one plain sequential node - same
    shape as proposal_drafting_node (no Send(), no gate/interrupt(): Part
    C's gate_type CHECK has no graph-review value, and R4's task_type enum
    lists graph_projection as a real task type, not a gate). Sits between
    proposal_drafting and final_gate - the pass-through seam proposal_
    drafting_node itself occupied until now.

    Unlike proposal_drafting_node, this node has no LLM call at all (B10 has
    no agent contract) - project_tender is deterministic, reading already-
    committed Postgres rows and writing to Neo4j. Runs once per tender, same
    "no per-task_id workflow_tasks row" reasoning as proposal_drafting_node's
    own docstring.

    Failure here is swallowed the same way proposal_drafting_node's is: a
    failed projection leaves the graph provably stale (graph_service.
    project_tender marks the run 'failed', which get_latest_completed_run's
    status filter already excludes), not silently wrong, and every later
    graph read correctly raises GraphStaleError rather than serving bad
    data - so the workflow can still safely reach final_gate even if
    projection failed this run.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])

    await sync_run_status(workflow_run_id, "projecting_graph")

    try:
        await project_tender(tender_id)
    except Exception as exc:
        # Swallowed the same way proposal_drafting_node's is, for the same
        # reason (no workflow_tasks row; the run should still reach
        # final_gate) - but a failed projection is otherwise invisible
        # outside a GraphStaleError raised by a LATER read, which nothing
        # in this run's own path ever triggers. degraded_stages surfaces it
        # at final_gate_node's own gate summary instead.
        logger.exception("graph projection failed for tender %s", tender_id)
        return {"degraded_stages": [f"graph_projection:{type(exc).__name__}"]}

    return {}
