"""Phase 4 plan item 77: a task reads Running while it runs, and no task is
left pending or running once its run has ended.

- A task node marks its task "running" as it starts
  (WorkflowRepository.mark_task_running, with a WorkflowTaskStarted event).
- When a run reaches a terminal status, every task of it still pending or
  running becomes "skipped" (user decision, 2026-11-14), on each of the
  three writes that end a run: update_run_status (graph completion and
  failure, Request changes, cancel), fail_lost_run (the reaper) and
  mark_failed_if_not_terminal (a job timeout).
- A scheduled retry puts a task still "running" back to "pending": the
  failed attempt's graph call has returned, so nothing runs it until the
  retry does.

The sibling cancelled by another branch's failure, through the real graph,
is in test_graph_execution.py.
"""

import pytest
from _helpers import make_ready_run_inputs
from sqlalchemy import select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import WorkflowTask
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import workflow_service

pytestmark = pytest.mark.asyncio


async def _run_with_tasks(email: str):
    """A queued run with three tasks: one running, one pending, one done."""
    registered, tender = await make_ready_run_inputs(email, f"{email} Org", f"{email} RFP")
    run = await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )
    async with UnitOfWork() as uow:
        tasks = {}
        for task_type in ("compliance_analysis", "talent_analysis", "commercial_validation"):
            tasks[task_type] = await uow.workflow.create_task(
                workflow_run_id=run.id,
                task_key=f"{task_type}:{run.id}",
                task_type=task_type,
                assigned_agent="test",
                priority=10,
                input_refs={},
            )
        await uow.commit()
    async with UnitOfWork() as uow:
        assert await uow.workflow.mark_task_running(tasks["compliance_analysis"].id)
        await uow.workflow.mark_task_completed(tasks["commercial_validation"].id, output_refs={})
        await uow.commit()
    return registered, run, {t: task.id for t, task in tasks.items()}


async def _statuses(run_id) -> dict[str, str]:
    async with UnitOfWork() as uow:
        return {t.task_type: t.status for t in await uow.workflow.list_tasks(run_id)}


async def test_a_started_task_reads_running_with_a_start_time_and_a_started_event():
    _, run, ids = await _run_with_tasks("task-started@example.com")

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(run.id, f"compliance_analysis:{run.id}")
        events = (
            (
                await uow.session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == ids["compliance_analysis"],
                        OutboxEvent.event_type == "WorkflowTaskStarted",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert task is not None
    assert task.status == "running"
    assert task.started_at is not None
    assert len(events) == 1
    assert events[0].aggregate_type == "workflow_task"
    assert events[0].payload == {
        "workflow_run_id": str(run.id),
        "task_id": str(ids["compliance_analysis"]),
        "task_type": "compliance_analysis",
    }


async def test_a_cancel_mid_task_leaves_no_task_pending_or_running():
    registered, run, _ = await _run_with_tasks("task-cancel@example.com")
    assert await _statuses(run.id) == {
        "compliance_analysis": "running",
        "talent_analysis": "pending",
        "commercial_validation": "completed",
    }

    cancelled = await workflow_service.cancel_workflow_run(
        run.id, registered.organization.id, registered.user.id
    )

    assert cancelled is not None and cancelled.status == "cancelled"
    assert await _statuses(run.id) == {
        "compliance_analysis": "skipped",
        "talent_analysis": "skipped",
        "commercial_validation": "completed",
    }


async def test_a_task_starting_after_its_run_was_cancelled_stays_skipped():
    registered, run, ids = await _run_with_tasks("task-start-after-cancel@example.com")
    await workflow_service.cancel_workflow_run(
        run.id, registered.organization.id, registered.user.id
    )

    async with UnitOfWork() as uow:
        started = await uow.workflow.mark_task_running(ids["talent_analysis"])
        await uow.commit()

    assert started is False
    assert (await _statuses(run.id))["talent_analysis"] == "skipped"


async def test_a_task_created_after_its_run_ended_is_not_started():
    """The run guard on its own: a node can create a task after a cancel
    (cancellation is checked at gates), and that pending task's start is
    refused because its run is no longer active."""
    registered, run, _ = await _run_with_tasks("task-created-late@example.com")
    await workflow_service.cancel_workflow_run(
        run.id, registered.organization.id, registered.user.id
    )
    async with UnitOfWork() as uow:
        late = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key=f"late:{run.id}",
            task_type="late_task",
            assigned_agent="test",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        assert not await uow.workflow.mark_task_running(late.id)
        await uow.commit()

    assert (await _statuses(run.id))["late_task"] == "pending"


async def test_a_skipped_task_is_not_started_even_while_its_run_is_active():
    """The "skipped" guard on its own. In the code a skipped task under an
    active run exists only inside the race this guard is for (a start whose
    UPDATE waited on the cancel's row lock: Postgres re-checks the WHERE on
    the row the cancel wrote, where the run check still sees the old run),
    so the state is set directly here."""
    _, run, ids = await _run_with_tasks("task-skipped-active@example.com")
    async with UnitOfWork() as uow:
        task = await uow.session.get(WorkflowTask, ids["talent_analysis"])
        assert task is not None
        task.status = "skipped"
        await uow.commit()

    async with UnitOfWork() as uow:
        assert not await uow.workflow.mark_task_running(ids["talent_analysis"])
        await uow.commit()

    assert (await _statuses(run.id))["talent_analysis"] == "skipped"


@pytest.mark.parametrize("ending", ["completed", "failed", "changes_requested"])
async def test_a_run_ending_through_its_status_write_leaves_no_task_pending_or_running(ending):
    _, run, _ = await _run_with_tasks(f"task-end-{ending}@example.com")

    async with UnitOfWork() as uow:
        assert await uow.workflow.update_run_status(run.id, ending)
        await uow.commit()

    statuses = await _statuses(run.id)
    assert statuses["compliance_analysis"] == "skipped"
    assert statuses["talent_analysis"] == "skipped"
    assert statuses["commercial_validation"] == "completed"


async def test_a_reaped_run_leaves_no_task_pending_or_running():
    _, run, _ = await _run_with_tasks("task-reaped@example.com")

    async with UnitOfWork() as uow:
        current = await uow.workflow.get_run(run.id)
        assert current is not None
        assert await uow.workflow.fail_lost_run(
            run.id, current.status, current.current_job_id, "WORKER_LOST"
        )
        await uow.commit()

    statuses = await _statuses(run.id)
    assert statuses["compliance_analysis"] == "skipped"
    assert statuses["talent_analysis"] == "skipped"
    assert statuses["commercial_validation"] == "completed"


async def test_a_timed_out_run_leaves_no_task_pending_or_running():
    _, run, _ = await _run_with_tasks("task-timeout@example.com")

    await workflow_service.mark_run_failed_on_timeout(run.id)

    statuses = await _statuses(run.id)
    assert statuses["compliance_analysis"] == "skipped"
    assert statuses["talent_analysis"] == "skipped"
    assert statuses["commercial_validation"] == "completed"


async def test_a_scheduled_retry_puts_a_running_task_back_to_pending():
    _, run, _ = await _run_with_tasks("task-retry@example.com")
    async with UnitOfWork() as uow:
        assert await uow.workflow.claim_queued_run(run.id, "start_job")
        await uow.commit()

    async with UnitOfWork() as uow:
        assert await uow.workflow.record_retry_job(run.id, "start_job", "retry_1")
        await uow.commit()

    assert await _statuses(run.id) == {
        "compliance_analysis": "pending",
        "talent_analysis": "pending",
        "commercial_validation": "completed",
    }


async def test_a_retry_not_recorded_leaves_the_tasks_as_they_were():
    _, run, _ = await _run_with_tasks("task-retry-refused@example.com")
    async with UnitOfWork() as uow:
        assert await uow.workflow.claim_queued_run(run.id, "start_job")
        await uow.commit()

    async with UnitOfWork() as uow:
        assert not await uow.workflow.record_retry_job(run.id, "some_other_job", "retry_1")
        await uow.commit()

    assert (await _statuses(run.id))["compliance_analysis"] == "running"
