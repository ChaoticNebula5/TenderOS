import uuid

from langgraph.types import interrupt

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import SpecialistTaskState, TenderState

# ruff/mypy will flag interrupt/sync_run_status/build_gate_payload/
# derive_gate_key as unused until requirement_gate_node's TODO(human) body
# below is filled in - expected, left in deliberately as the "already in
# scope" imports the docstring describes.


async def requirement_stub_node(state: TenderState) -> dict:
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    task_key = f"requirement_extraction:{workflow_run_id}"

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(workflow_run_id, task_key)

        if task is None:
            task = await uow.workflow.create_task(
                workflow_run_id=workflow_run_id,
                task_key=task_key,
                task_type="requirement_extraction",
                assigned_agent="requirements",
                priority=10,
                input_refs={},
            )
        await uow.workflow.mark_task_completed(task.id, output_refs={})
        await uow.commit()

    return {"status": "awaiting_requirement_review"}


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

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "requirement_review",
                workflow_run_id,
                targets=[],
                summary={},
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=uuid.UUID(state["tender_id"]),
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

    await sync_run_status(workflow_run_id, "running_specialists")
    return {"status": "running_specialists"}


async def compliance_stub_node(state: SpecialistTaskState) -> dict:
    task_id = uuid.UUID(state["task_id"])

    async with UnitOfWork() as uow:
        await uow.workflow.mark_task_completed(
            task_id, output_refs={"summary": "B3 compliance stub completed."}
        )
        await uow.commit()

    return {
        "completed_task_ids": [str(task_id)],
    }


async def talent_stub_node(state: SpecialistTaskState) -> dict:
    task_id = uuid.UUID(state["task_id"])

    async with UnitOfWork() as uow:
        await uow.workflow.mark_task_completed(
            task_id, output_refs={"summary": "B3 talent stub completed."}
        )
        await uow.commit()

    return {
        "completed_task_ids": [str(task_id)],
    }


async def commercial_stub_node(state: SpecialistTaskState) -> dict:
    task_id = uuid.UUID(state["task_id"])

    async with UnitOfWork() as uow:
        await uow.workflow.mark_task_completed(
            task_id, output_refs={"summary": "B3 commercial stub completed."}
        )
        await uow.commit()

    return {
        "completed_task_ids": [str(task_id)],
    }
