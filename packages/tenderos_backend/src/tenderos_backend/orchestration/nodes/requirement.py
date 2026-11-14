import uuid

from langgraph.types import interrupt

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_summary_service
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import TenderState


async def requirement_gate_node(state: TenderState) -> dict:
    """R5's requirement_review human gate - the workflow's first interrupt()."""

    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key(
        "requirement_review",
        state["plan_version"],
    )

    if await is_cancelled(workflow_run_id):
        # I6: don't open a new gate for a run that's already been cancelled.
        # Deliberately no sync_run_status call here - workflow_runs.status is
        # already "cancelled" (set by cancel_workflow_run) and must stay that
        # way, not get overwritten to "failed" by the normal reject path below.
        return {"status": "rejected"}

    tender_id = uuid.UUID(state["tender_id"])

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            # Runs exactly once - interrupt() below replays this whole
            # function on resume, but by then `gate` is no longer None, so
            # this block (and the query it depends on) never re-runs. Safe to
            # read requirements here because requirement_extraction_node
            # already committed them earlier in this same graph execution,
            # before this node runs at all.
            requirements = await uow.requirements.list_for_tender(tender_id)

            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "requirement_review",
                workflow_run_id,
                targets=[
                    {"target_type": "requirement", "target_id": str(r.id)} for r in requirements
                ],
                # Plan item 73: the same function the gates API uses for
                # the live counts (gate_summary_service).
                summary=await gate_summary_service.requirement_review_summary(uow, tender_id),
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="requirement_review",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_requirement_review")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as reject - do not advance to specialist_dispatch.
        # route_after_requirement_gate routes "changes_requested" to END,
        # same as "rejected". Does not yet re-open this gate for a
        # corrective resubmission - see final_gate_node's identical note.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    await sync_run_status(workflow_run_id, "running_specialists")
    return {"status": "running_specialists"}
