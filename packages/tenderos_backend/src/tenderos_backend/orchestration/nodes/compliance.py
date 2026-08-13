import logging
import uuid

from langgraph.types import interrupt
from redis.asyncio import Redis as AsyncRedis

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.compliance_service import evaluate_compliance_for_requirement
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import SpecialistTaskState, TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape
    # requirement_extraction_node's own helper documents - tests monkeypatch
    # this one name instead of touching a real GeminiClient.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


async def compliance_analysis_node(state: SpecialistTaskState) -> dict:
    """Replaces B3's compliance_stub_node - runs compliance_service.py's
    full per-requirement pipeline (retrieve -> propose -> materialize ->
    enforce -> persist) for every requirement in the tender, sequentially
    for the same free-tier rate-limit reason requirement_extraction_node's
    mandatory-verification pass already is. One requirement's failure is
    logged and skipped, not fatal to the rest - the task itself only fails
    if something before the per-requirement loop breaks (e.g. the tender
    can't be loaded at all).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])
    task_id = uuid.UUID(state["task_id"])

    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)

    try:
        client = _build_llm_client()
        evaluated = 0
        for requirement in requirements:
            try:
                await evaluate_compliance_for_requirement(
                    client,
                    settings.reasoning_model,
                    requirement.id,
                    workflow_run_id=workflow_run_id,
                    task_id=task_id,
                )
                evaluated += 1
            except Exception:
                logger.exception(
                    "compliance analysis failed for requirement %s", requirement.id
                )

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(
                task_id,
                output_refs={
                    "requirement_count": len(requirements),
                    "evaluated_count": evaluated,
                },
            )
            await uow.commit()

    except Exception as exc:
        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_failed(task_id, error_code=type(exc).__name__)
            await uow.commit()
        raise

    return {"completed_task_ids": [str(task_id)]}


async def compliance_gate_node(state: TenderState) -> dict:
    """R5's compliance_review human gate.

    Sits AFTER specialist_join, BEFORE final_gate - deliberately NOT nested
    inside compliance_analysis's own Send()-dispatched branch (that was the
    original B7 design; caught live via a step-by-step debug harness that it
    was broken). An interrupt()-bearing node placed upstream of a Send()
    fan-in join replays its entire function - and therefore re-delivers a
    fresh trigger into the join - every time ANY interrupt further down that
    same un-Send()'d continuation gets resumed (final_gate's own interrupt()
    replaying included). That produced a phantom second run of
    specialist_join -> final_gate on final_readiness's own resume, leaving
    the workflow permanently stuck ("waiting_for_human" even after final_gate
    had already set the run to "ready"). Moving this node to the same
    sequential slot final_gate already safely occupies - a plain node after
    the join, never inside a Send()'d branch - removes the replay path
    entirely, the same reason final_gate_node itself never had this problem.

    Otherwise unchanged in shape from the original design: deterministic
    gate_key from plan_version, create the gate row only on genuine first
    creation, interrupt(), route on the human's decision.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key("compliance_review", state["plan_version"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    tender_id = uuid.UUID(state["tender_id"])

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
            pending = [e for e in entries if not e.human_approved]
            # "high risk" here means a status PRD 8.4 would never let stand
            # unapproved without evidence - not a separate ML-scored risk
            # tier, just the two statuses that most need a human's eyes
            # before this tender proceeds.
            high_risk = [
                e for e in pending if e.status in ("evidence_unavailable", "non_compliant")
            ]

            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "compliance_review",
                workflow_run_id,
                targets=[
                    {"target_type": "compliance_entry", "target_id": str(e.id)} for e in entries
                ],
                summary={
                    "compliance_entry_count": len(entries),
                    "pending_count": len(pending),
                    "high_risk_count": len(high_risk),
                },
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="compliance_review",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_compliance_review")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        # Known limitation, not an oversight - see KNOWN_LIMITATIONS.md
        # "[B7] Compliance gate rejection kills the whole run": this mirrors
        # requirement_gate_node's own reject behavior (the only precedent in
        # this codebase), but talent_stub/commercial_stub are still stubs -
        # once B8 makes them real, rejecting here will also discard whatever
        # they already completed in this same run.
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}

    # No status write on approve - final_gate_node, its unconditional next
    # node with no Send() boundary in between, sets its own
    # "awaiting_final_approval" value moments later, and nothing routes on
    # any particular non-"rejected" value here. Same reasoning already
    # applied to specialist_join_node: two plain-edge-chained nodes both
    # writing "status" in one continuous, non-Send()-separated execution
    # can land in the same LangGraph superstep and collide.
    return {}
