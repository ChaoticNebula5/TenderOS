import uuid

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState
from tenderos_backend.orchestration.supervisor import build_plan


async def supervisor_node(state: TenderState) -> dict:
    """Persists orchestration.supervisor.build_plan's (stubbed) task list as
    workflow_tasks rows, then advances status toward requirement processing.

    dispatch_task_ids is returned in the same fixed order build_plan emits
    (compliance_analysis, talent_analysis, commercial_validation) - the later
    Send-based dispatch node pairs each id with its specialist node positionally,
    since B3's plan is a hardcoded 3-task list, not something dispatch needs to
    re-derive from task_type at runtime.

    Real replanning re-entry (R2.3/B2.3) isn't wired until B11, so this node only
    ever runs once per B3 workflow - get_task_by_key's dedup-by-key check exists
    for when a second entry becomes possible, not because B3 exercises it.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    plan = build_plan(state["workflow_run_id"])
    next_plan_version = state["plan_version"] + 1

    dispatch_task_ids: list[str] = []
    async with UnitOfWork() as uow:
        for task in plan:
            existing = await uow.workflow.get_task_by_key(workflow_run_id, task["task_key"])
            if existing is not None:
                dispatch_task_ids.append(str(existing.id))
                continue
            created = await uow.workflow.create_task(
                workflow_run_id=workflow_run_id,
                task_key=task["task_key"],
                task_type=task["task_type"],
                assigned_agent=task["assigned_agent"],
                priority=task["priority"],
                input_refs=task["input_refs"],
            )
            dispatch_task_ids.append(str(created.id))
        # B16: workflow_runs.plan_version used to stay 0 forever - only the
        # checkpoint's own copy (returned below) ever advanced. Written in
        # the same transaction as the task rows above, not a separate
        # round trip.
        await uow.workflow.update_plan_version(workflow_run_id, next_plan_version)
        await uow.commit()

    await sync_run_status(workflow_run_id, "extracting_requirements")
    return {
        "status": "extracting_requirements",
        "plan_version": next_plan_version,
        "dispatch_task_ids": dispatch_task_ids,
    }
