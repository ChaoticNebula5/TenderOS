import uuid

from langgraph.types import interrupt

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import TenderState

# ruff/mypy will flag the imports above as unused until the TODO(human) body
# below is filled in - expected, since nothing calls them yet. They're left
# in deliberately (matching the "already in scope" list in the docstring)
# rather than stripped, so uuid/interrupt/UnitOfWork/sync_run_status/
# build_gate_payload/derive_gate_key are all one import already done, not
# something to go add yourself.


async def final_gate_node(state: TenderState) -> dict:
    """R5's final_readiness human gate - B3's stand-in for the eventual full
    review_router/readiness pipeline (R2's nodes between specialist_join and
    this one arrive with B9-B12; see the plan's "seam" note)."""

    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key(
        "final_readiness",
        state["plan_version"],
    )

    if await is_cancelled(workflow_run_id):
        # I6: don't open a new gate for a run that's already been cancelled.
        # No sync_run_status call here either - see requirement_gate_node's
        # identical comment.
        return {"status": "rejected"}

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "final_readiness",
                workflow_run_id,
                targets=[],
                summary={},
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=uuid.UUID(state["tender_id"]),
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="final_readiness",
                request_payload=payload,
            )

        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_final_approval")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}

    await sync_run_status(workflow_run_id, "ready")
    return {"status": "ready"}
