import logging
import uuid

from langgraph.types import interrupt

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.commercial_service import evaluate_commercial_for_tender
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import SpecialistTaskState, TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape compliance_analysis_
    # node's and talent_analysis_node's own helpers document. B17: delegates
    # to agents/llm/factory.py::build_llm_client - still resolves its own
    # key first (B16: commercial_gemini_api_key first, falling back to the
    # shared gemini_api_key when unset - see Settings.commercial_gemini_api_key's
    # own comment for why), used only in real-provider mode.
    return build_llm_client(api_key=settings.commercial_gemini_api_key or settings.gemini_api_key)


async def commercial_analysis_node(state: SpecialistTaskState) -> dict:
    """Replaces B3's commercial_stub_node. A single call into
    evaluate_commercial_for_tender covers ingestion, every deterministic
    check across every pricing document in the tender, and LLM explanation
    + persistence - unlike compliance/talent's per-requirement/per-role
    loops, there's no natural per-item unit to retry independently here
    (cross_doc_mismatch inherently needs every sheet's data at once), so
    one failure anywhere in the pipeline fails the whole task rather than
    being logged and skipped.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])
    task_id = uuid.UUID(state["task_id"])

    try:
        client = _build_llm_client()
        findings = await evaluate_commercial_for_tender(
            client,
            settings.reasoning_model,
            tender_id,
            workflow_run_id=workflow_run_id,
            task_id=task_id,
        )

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(
                task_id, output_refs={"finding_count": len(findings)}
            )
            await uow.commit()

    except Exception as exc:
        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_failed(task_id, error_code=type(exc).__name__)
            await uow.commit()
        raise

    return {"completed_task_ids": [str(task_id)]}


async def commercial_approval_gate_node(state: TenderState) -> dict:
    """R5's commercial_approval human gate. Sits AFTER resource_selection_
    gate, in the same safe sequential slot every post-join gate uses - never
    nested inside commercial_analysis's own Send()-dispatched branch (B7's
    own topology bug: an interrupt()-bearing node upstream of a Send()
    fan-in replays on every later, unrelated resume). Otherwise identical
    shape to compliance_gate_node/resource_selection_gate_node:
    deterministic gate_key from plan_version, create-once, interrupt(),
    route on decision, no status write on approve (final_gate_node sets its
    own status moments later in the same un-Send()'d continuation).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key("commercial_approval", state["plan_version"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    tender_id = uuid.UUID(state["tender_id"])

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
            pending = [f for f in findings if not f.resolved]
            critical = [f for f in pending if f.severity == "critical"]

            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "commercial_approval",
                workflow_run_id,
                targets=[
                    {"target_type": "pricing_finding", "target_id": str(f.id)} for f in findings
                ],
                summary={
                    "finding_count": len(findings),
                    "pending_count": len(pending),
                    "critical_count": len(critical),
                },
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="commercial_approval",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_commercial_approval")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        # KNOWN_LIMITATIONS.md: "[B7/B8] Post-join gate rejection kills the
        # whole run" - same accepted, B11-scoped limitation the other two
        # post-join gates already carry.
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as reject - do not advance to proposal_drafting.
        # route_after_commercial_approval_gate routes "changes_requested" to
        # END, same as "rejected". Does not yet re-open this gate for a
        # corrective resubmission - see final_gate_node's identical note.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    return {}
