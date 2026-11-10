"""Plan item 54: the SSE stream against commit order and clock steps, real
Postgres.

Until item 54, outbox_stream.py remembered the created_at of the last event
it sent and next asked only for later ones. created_at is Postgres now(),
the time the writing transaction began, but a row is visible only once its
transaction commits, so all three tests below failed: the stream skipped
the event for good. The stream now tracks the ids it has sent. Each test
fixes the order of begin, commit and poll by awaiting every step before the
next (separate sessions, no sleeps), checks its own setup, and then that
the event still arrives.
"""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from tenderos_api import outbox_stream
from tenderos_api.routes.workflow import _workflow_progress_stream
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_WORKFLOW_TASK_COMPLETED

pytestmark = pytest.mark.asyncio

HEARTBEAT = ": heartbeat\n\n"


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outbox_stream, "POLL_INTERVAL_SECONDS", 0.05)


async def _run_with_two_tasks(name: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.commit()
        tender_id = tender.id
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        tasks = [
            await uow.workflow.create_task(
                workflow_run_id=run.id,
                task_key=key,
                task_type=key,
                assigned_agent=key,
                priority=10,
                input_refs={},
            )
            for key in ("compliance_analysis", "talent_analysis")
        ]
        await uow.commit()
    return run.id, tasks[0].id, tasks[1].id


async def _completed_task_ids(gen, chunks: int) -> list[str]:
    """The task ids of task.completed messages among the next `chunks`
    chunks (one chunk per event, or a heartbeat for an empty poll)."""
    seen = []
    for _ in range(chunks):
        chunk = await anext(gen)
        if chunk.startswith("data:"):
            message = json.loads(chunk.removeprefix("data:").strip())
            if message["event_type"] == "task.completed":
                seen.append(message["task_id"])
    return seen


async def _completion_stamps(*task_ids: uuid.UUID) -> list:
    async with UnitOfWork() as uow:
        rows = (
            await uow.session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == EVENT_WORKFLOW_TASK_COMPLETED)
            )
        ).scalars()
        by_task = {row.payload["task_id"]: row.created_at for row in rows}
    return [by_task[str(task_id)] for task_id in task_ids]


async def test_an_event_committed_after_a_later_stamped_one_still_reaches_the_stream():
    """Two specialist branches finishing together: the one whose
    transaction began first commits second."""
    run_id, first_task, second_task = await _run_with_two_tasks("commit order")
    gen = await _workflow_progress_stream(run_id)
    try:
        assert await anext(gen) == HEARTBEAT  # the stream's starting point is set

        async with UnitOfWork() as slow:
            await slow.workflow.mark_task_completed(first_task, output_refs={})
            async with UnitOfWork() as fast:
                await fast.workflow.mark_task_completed(second_task, output_refs={})
                await fast.commit()
            assert await _completed_task_ids(gen, chunks=3) == [str(second_task)]
            await slow.commit()

        slow_stamp, fast_stamp = await _completion_stamps(first_task, second_task)
        assert slow_stamp < fast_stamp  # the setup really made begin order != commit order

        delivered = await _completed_task_ids(gen, chunks=10)
        assert str(first_task) in delivered, (
            "the event that committed second, with the earlier created_at, never reached the stream"
        )
    finally:
        await gen.aclose()


async def test_an_event_stamped_before_one_already_sent_still_reaches_the_stream():
    """The database clock steps back (Docker Desktop's VM clock does, by
    about a second at a time): a later event gets an earlier created_at
    than one the stream has already sent. Simulated by moving the later
    event's stamp back 5 s before it commits."""
    run_id, first_task, second_task = await _run_with_two_tasks("clock step")
    gen = await _workflow_progress_stream(run_id)
    try:
        assert await anext(gen) == HEARTBEAT

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(first_task, output_refs={})
            await uow.commit()
        assert await _completed_task_ids(gen, chunks=3) == [str(first_task)]

        async with UnitOfWork() as uow:
            await uow.workflow.mark_task_completed(second_task, output_refs={})
            await uow.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.event_type == EVENT_WORKFLOW_TASK_COMPLETED,
                    OutboxEvent.aggregate_id == second_task,
                )
                .values(created_at=OutboxEvent.created_at - timedelta(seconds=5))
            )
            await uow.commit()

        delivered = await _completed_task_ids(gen, chunks=10)
        assert str(second_task) in delivered, (
            "the event stamped before one already sent never reached the stream"
        )
    finally:
        await gen.aclose()


async def test_an_event_from_a_transaction_open_when_the_stream_starts_still_reaches_it():
    """A viewer opens the page while a task's completion is being written:
    the transaction began before the stream's starting point and commits
    after it."""
    run_id, task, _ = await _run_with_two_tasks("stream start")
    async with UnitOfWork() as slow:
        await slow.workflow.mark_task_completed(task, output_refs={})
        gen = await _workflow_progress_stream(run_id)
        assert await anext(gen) == HEARTBEAT  # starting point set while slow is open
        await slow.commit()
    try:
        delivered = await _completed_task_ids(gen, chunks=10)
        assert str(task) in delivered, (
            "the event whose transaction spanned the stream's start never reached the stream"
        )
    finally:
        await gen.aclose()
