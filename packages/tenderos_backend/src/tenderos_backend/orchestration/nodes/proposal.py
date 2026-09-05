import logging
import uuid

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.domain.services.proposal_service import draft_proposal_for_tender
from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape every other
    # analysis node's own helper uses. B17: delegates to agents/llm/
    # factory.py::build_llm_client, the real gemini-vs-fake decision.
    return build_llm_client()


async def proposal_drafting_node(state: TenderState) -> dict:
    """R2's proposal_subgraph, reduced to one plain sequential node (no
    Send(), no gate/interrupt() - Part C's gate_type CHECK constraint has no
    'proposal_review' value, and R2's own topology places proposal drafting
    before the finding/readiness gates, not gated itself). Sits in the same
    non-Send()'d slot the three post-join gates already occupy, between
    commercial_approval_gate and final_gate - the "pass-through seam"
    flagged in the B8 plan.

    Unlike compliance_analysis/talent_analysis/commercial_analysis, this
    node is not Send()-dispatched per branch: it runs once per tender,
    consuming the combined output of all three specialist branches at once,
    so there is no per-task_id to report against (no workflow_tasks row
    exists for it, the same reason specialist_join_node and final_gate_node
    never call uow.workflow.mark_task_completed either). It's safe for this
    node to write its own status directly (unlike compliance_gate/
    resource_selection_gate's deliberate omission) - there is no adjacent
    node about to make this particular write redundant the way
    resource_selection_gate sits next to compliance_gate.

    No is_cancelled check, matching every other *_analysis_node (not gate
    node) - cancellation is checked at dispatch/gate boundaries, not inside
    already-committed analysis work.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])

    await sync_run_status(workflow_run_id, "drafting")

    try:
        client = _build_llm_client()
        await draft_proposal_for_tender(
            client, settings.reasoning_model, tender_id, workflow_run_id=workflow_run_id
        )
    except Exception as exc:
        # Logged and swallowed, not re-raised: this node has no workflow_
        # tasks row to mark failed (see docstring - it's not Send()-
        # dispatched), and the workflow should still be able to reach
        # final_gate even if drafting hit a problem for this run. But
        # swallowing silently used to mean a run could reach "ready" with
        # zero proposal sections and nothing in run state said so -
        # degraded_stages carries this forward to final_gate_node's own
        # gate summary instead (see TenderState's own comment on the field).
        logger.exception("proposal drafting failed for tender %s", tender_id)
        return {"degraded_stages": [f"proposal_drafting:{type(exc).__name__}"]}

    return {}
