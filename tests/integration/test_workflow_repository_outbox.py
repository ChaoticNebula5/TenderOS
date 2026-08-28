"""db/repositories/workflow.py's own outbox writes (B14) - the 4 choke
points (update_run_status, create_task, mark_task_completed,
mark_task_failed) each write a sibling outbox row inside the same
repository method, not at each of their ~36 real call sites across
orchestration/nodes/ (see that repository's own module docstring for why).
The full suite's existing orchestration-node tests already prove these 4
methods keep working with the outbox write added - this file is the
direct assertion that the write itself is correct, not just that nothing
broke."""

import uuid

import pytest
from sqlalchemy import select
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _make_org_and_tender(name: str) -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return tender.id


async def _events_for(aggregate_id: uuid.UUID) -> list[OutboxEvent]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == aggregate_id)
        )
        return list(result.scalars().all())


async def test_update_run_status_writes_a_workflow_run_status_changed_event():
    tender_id = await _make_org_and_tender("Run Status Outbox Test")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()

    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run.id, "running")
        await uow.commit()

    events = await _events_for(run.id)
    status_events = [e for e in events if e.event_type == "WorkflowRunStatusChanged"]
    assert len(status_events) == 1
    assert status_events[0].aggregate_type == "workflow_run"
    assert status_events[0].payload == {"workflow_run_id": str(run.id), "status": "running"}

    # A second status change must not collide on the idempotency_key -
    # sync_run_status calls this many times per real run (~19 on a happy
    # path with 2 revision passes), and a collision here would raise
    # IntegrityError inside the same transaction as the real status write.
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run.id, "completed")
        await uow.commit()
    events = await _events_for(run.id)
    assert len([e for e in events if e.event_type == "WorkflowRunStatusChanged"]) == 2


async def test_create_task_writes_a_workflow_task_created_event():
    tender_id = await _make_org_and_tender("Task Created Outbox Test")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()

    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key="requirement_extraction",
            task_type="requirement_extraction",
            assigned_agent="requirements",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    events = await _events_for(task.id)
    assert len(events) == 1
    assert events[0].event_type == "WorkflowTaskCreated"
    assert events[0].aggregate_type == "workflow_task"
    assert events[0].payload == {
        "workflow_run_id": str(run.id),
        "task_id": str(task.id),
        "task_type": "requirement_extraction",
    }


async def test_mark_task_completed_and_failed_each_write_their_own_event():
    tender_id = await _make_org_and_tender("Task Terminal Outbox Test")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        completed_task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key="compliance_analysis",
            task_type="compliance_analysis",
            assigned_agent="evidence_compliance",
            priority=10,
            input_refs={},
        )
        failed_task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key="talent_analysis",
            task_type="talent_analysis",
            assigned_agent="talent",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        await uow.workflow.mark_task_completed(completed_task.id, output_refs={"count": 1})
        await uow.commit()
    async with UnitOfWork() as uow:
        await uow.workflow.mark_task_failed(failed_task.id, error_code="ProviderTimeoutError")
        await uow.commit()

    completed_events = await _events_for(completed_task.id)
    completed = [e for e in completed_events if e.event_type == "WorkflowTaskCompleted"]
    assert len(completed) == 1
    assert completed[0].payload == {
        "workflow_run_id": str(run.id),
        "task_id": str(completed_task.id),
        "task_type": "compliance_analysis",
    }

    failed_events = await _events_for(failed_task.id)
    failed = [e for e in failed_events if e.event_type == "WorkflowTaskFailed"]
    assert len(failed) == 1
    assert failed[0].payload == {
        "workflow_run_id": str(run.id),
        "task_id": str(failed_task.id),
        "task_type": "talent_analysis",
    }
