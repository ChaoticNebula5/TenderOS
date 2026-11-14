import logging
import uuid

from langgraph.types import interrupt

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_summary_service
from tenderos_backend.domain.services.compliance_service import evaluate_compliance_for_requirement
from tenderos_backend.domain.services.workflow_service import (
    is_cancelled,
    start_task,
    sync_run_status,
)
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import SpecialistTaskState, TenderState

logger = logging.getLogger(__name__)


class AllItemsFailedError(Exception):
    """Raised when every requirement in a non-empty batch failed - distinct
    from an empty tender (0 requirements, evaluated=0 is correct there) and
    from a partial failure (some requirements succeed, task still
    legitimately completes). Caught by this node's own outer except, same
    as any other exception - not a second failure path, just a real signal
    for what would otherwise be indistinguishable from full success: a
    completed task with output_refs showing evaluated_count=0."""

    pass


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape
    # requirement_extraction_node's own helper documents - tests monkeypatch
    # this one name instead of touching a real GeminiClient. B17: the actual
    # gemini-vs-fake decision now lives in agents/llm/factory.py::
    # build_llm_client - this delegates to it, still resolving ITS OWN key
    # first (B16: compliance_gemini_api_key first, falling back to the
    # shared gemini_api_key when unset - see Settings.compliance_gemini_api_key's
    # own comment for why this branch specifically needs its own key; the
    # factory only uses this value in real-provider mode, ignored entirely
    # in fake mode).
    return build_llm_client(api_key=settings.compliance_gemini_api_key or settings.gemini_api_key)


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

    await start_task(task_id)
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
                logger.exception("compliance analysis failed for requirement %s", requirement.id)

        if requirements and evaluated == 0:
            # Every requirement in a non-empty batch failed - a completed
            # task with evaluated_count=0 would be indistinguishable from a
            # tender with zero requirements, and B12's readiness report
            # reads run/task state, not per-item logs. Raising here routes
            # through the existing except-block below (mark_task_failed +
            # re-raise), not a second failure path.
            raise AllItemsFailedError(
                f"all {len(requirements)} requirements failed compliance analysis"
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

            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "compliance_review",
                workflow_run_id,
                targets=[
                    {"target_type": "compliance_entry", "target_id": str(e.id)} for e in entries
                ],
                # Plan item 73: shared with the gates API's live counts.
                summary=await gate_summary_service.compliance_review_summary(uow, tender_id),
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
        # "[B7/B8] Post-join gate rejection kills the whole run": mirrors
        # requirement_gate_node's reject behavior, now shared by all three
        # post-join gates (compliance_review, resource_selection,
        # commercial_approval) - accepted, real fix is B11's bounded
        # revision loop, not revisited again before then.
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as reject - do not advance to resource_selection_gate.
        # route_after_compliance_gate routes "changes_requested" to END,
        # same as "rejected". Does not yet re-open this gate for a
        # corrective resubmission - see final_gate_node's identical note.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    # No status write on approve - resource_selection_gate_node, the next
    # node in this same un-Send()'d continuation, sets its own
    # "awaiting_resource_selection" value moments later, and nothing routes on
    # any particular non-"rejected" value here. Same reasoning already
    # applied to specialist_join_node: two plain-edge-chained nodes both
    # writing "status" in one continuous, non-Send()-separated execution
    # can land in the same LangGraph superstep and collide.
    return {}
