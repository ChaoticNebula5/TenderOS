"""Plan item 59, fix 5 (PR 20): a task whose node ends after its run was
cancelled.

A cancel ends the run and marks its unfinished tasks "skipped"
(WorkflowRepository._skip_unfinished_tasks, in the cancel's transaction).
Cancellation is checked at the gates, not inside an analysis node, so a node
already working goes on and ends with mark_task_completed or
mark_task_failed. Each used to write the task whatever its status, so a task
of a cancelled run read Completed or Failed, with a task event, under a run
that read Cancelled. Now each is one conditional UPDATE, written only while
the task is pending or running (user decision Q3 a): after a cancel the task
stays skipped, with no task event and one INFO line, and what else the
node's transaction wrote is kept.

Steps: the cancel's transaction is left open; the node's end starts in
another session and waits on the task row's lock; the cancel commits; the
node's end then finds the task skipped.
"""

import asyncio
import logging
import uuid

import pytest
from _helpers import register_org_and_tender
from _lock_waits import waiting
from sqlalchemy import func, select
from tenderos_backend.db.models.document import DocumentChunk
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import WorkflowTask
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    EVENT_WORKFLOW_TASK_COMPLETED,
    EVENT_WORKFLOW_TASK_FAILED,
)

pytestmark = pytest.mark.asyncio

_WORKFLOW_LOGGER = "tenderos_backend.db.repositories.workflow"


async def _running_task() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A run with one running task, and a chunk of the bid's document for the
    node's other write. Returns (run, task, chunk)."""
    email = f"task-end-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} bid")
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender.id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder shall hold ISO 9001.",
            normalized_text="the bidder shall hold iso 9001.",
            content_hash=uuid.uuid4().hex,
        )
        run = await uow.workflow.create_run(tender.id, "v1", {})
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key=f"requirement_extraction:{run.id}",
            task_type="requirement_extraction",
            assigned_agent="requirements",
            priority=10,
            input_refs={},
        )
        assert await uow.workflow.mark_task_running(task.id)
        await uow.commit()
        return run.id, task.id, chunk.id


async def _task(task_id: uuid.UUID) -> WorkflowTask:
    async with UnitOfWork() as uow:
        task = await uow.session.get(WorkflowTask, task_id)
        assert task is not None
        return task


async def _task_events(task_id: uuid.UUID, event_type: str) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.aggregate_id == task_id, OutboxEvent.event_type == event_type)
            )
        ).scalar_one()


async def _extracted_by(chunk_id: uuid.UUID) -> uuid.UUID | None:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(DocumentChunk.extracted_by_task_id).where(DocumentChunk.id == chunk_id)
            )
        ).scalar_one()


async def _end_after_a_cancel(run_id: uuid.UUID, end) -> object:
    """The cancel's transaction held open while `end` (the node's last
    transaction) starts; `end` must wait on the task row, then the cancel
    commits. Returns what `end` returned."""
    async with UnitOfWork() as cancel:
        assert await cancel.workflow.update_run_status(run_id, "cancelled")
        node = asyncio.create_task(end())
        try:
            await waiting(node, "the node's end never waited for the cancel")
        finally:
            await cancel.commit()
    return await asyncio.wait_for(node, timeout=10)


async def test_a_completion_after_a_cancel_leaves_the_task_skipped_and_keeps_its_work(caplog):
    """The extraction node's last transaction: the task's completion and the
    chunks it read, marked together. After a cancel the completion writes
    nothing and no event, and the chunk mark is kept."""
    run_id, task_id, chunk_id = await _running_task()
    caplog.set_level(logging.INFO, logger=_WORKFLOW_LOGGER)

    async def complete() -> bool:
        async with UnitOfWork() as uow:
            written = await uow.workflow.mark_task_completed(
                task_id, output_refs={"requirement_count": 3}
            )
            await uow.documents.mark_chunks_extracted([chunk_id], task_id)
            await uow.commit()
            return written

    written = await _end_after_a_cancel(run_id, complete)

    task = await _task(task_id)
    assert (task.status, task.output_refs, task.completed_at) == ("skipped", {}, None)
    assert written is False
    assert await _task_events(task_id, EVENT_WORKFLOW_TASK_COMPLETED) == 0
    assert await _extracted_by(chunk_id) == task_id, "the node's other write is kept"
    lines = [r for r in caplog.records if r.name == _WORKFLOW_LOGGER and str(task_id) in r.message]
    assert [r.levelno for r in lines] == [logging.INFO]


async def test_a_failure_after_a_cancel_leaves_the_task_skipped_and_its_attempts_as_they_were(
    caplog,
):
    run_id, task_id, _chunk_id = await _running_task()
    attempts = (await _task(task_id)).attempt_count
    caplog.set_level(logging.INFO, logger=_WORKFLOW_LOGGER)

    async def fail() -> bool:
        async with UnitOfWork() as uow:
            written = await uow.workflow.mark_task_failed(task_id, error_code="TimeoutError")
            await uow.commit()
            return written

    written = await _end_after_a_cancel(run_id, fail)

    task = await _task(task_id)
    assert (task.status, task.error_code, task.completed_at) == ("skipped", None, None)
    assert task.attempt_count == attempts
    assert written is False
    assert await _task_events(task_id, EVENT_WORKFLOW_TASK_FAILED) == 0
    lines = [r for r in caplog.records if r.name == _WORKFLOW_LOGGER and str(task_id) in r.message]
    assert [r.levelno for r in lines] == [logging.INFO]


async def test_a_task_still_running_completes_and_fails_as_before():
    """The control: with no cancel, completion and failure write the task
    and its event, and a failure counts an attempt."""
    _run_id, task_id, _chunk_id = await _running_task()
    async with UnitOfWork() as uow:
        assert await uow.workflow.mark_task_failed(task_id, error_code="TimeoutError") is True
        await uow.commit()
    failed = await _task(task_id)
    assert (failed.status, failed.error_code) == ("failed", "TimeoutError")
    assert await _task_events(task_id, EVENT_WORKFLOW_TASK_FAILED) == 1

    async with UnitOfWork() as uow:
        assert await uow.workflow.mark_task_running(task_id)
        assert await uow.workflow.mark_task_completed(task_id, output_refs={"n": 1}) is True
        await uow.commit()
    done = await _task(task_id)
    assert (done.status, done.output_refs) == ("completed", {"n": 1})
    assert done.attempt_count == failed.attempt_count
    assert await _task_events(task_id, EVENT_WORKFLOW_TASK_COMPLETED) == 1
