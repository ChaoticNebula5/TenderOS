import logging
import uuid

from redis.asyncio import Redis as AsyncRedis

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.domain.services.proposal_service import draft_proposal_for_tender
from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape every other
    # analysis node's own helper uses.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


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

    client = _build_llm_client()
    try:
        await draft_proposal_for_tender(
            client, settings.reasoning_model, tender_id, workflow_run_id=workflow_run_id
        )
    except Exception:
        # Logged and swallowed, not re-raised: this node has no workflow_
        # tasks row to mark failed (see docstring - it's not Send()-
        # dispatched), and the workflow should still be able to reach
        # final_gate even if drafting hit a problem for this run - the
        # persisted proposal_sections/proposal_claims rows (or absence of
        # them) and agent_traces are the record of what happened, not a
        # field on TenderState.
        logger.exception("proposal drafting failed for tender %s", tender_id)

    return {}
