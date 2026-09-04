import asyncio

import pytest
from _helpers import make_ready_run_inputs
from redis import Redis
from rq import Queue
from sqlalchemy import select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    auth_service,
    tender_service,
    workflow_service,
)
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, _dispatch_once
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio


async def test_dispatcher_marks_event_dispatched_on_success():
    """Depends on outbox_dispatcher's retry/backoff TODO being implemented -
    currently raises NotImplementedError on every pending event."""
    registered = await auth_service.register(
        email="dispatch@example.com", password="hunter2", organization_name="Dispatch Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="Dispatch Tender",
        created_by=registered.user.id,
    )

    await _dispatch_once()

    async with UnitOfWork() as uow:
        still_pending = await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)

    assert all(e.aggregate_id != tender.id for e in still_pending)


async def test_workflow_run_created_routes_to_the_workflow_queue():
    """The routing table, not just generic dispatch success - WorkflowRunCreated
    must land on the "workflow" queue running run_workflow, not "maintenance"
    running the generic process_outbox_event placeholder every other event
    type still falls back to."""
    registered, tender = await make_ready_run_inputs(
        "dispatch-routing@example.com", "Routing Org", "Routing Tender"
    )
    run = await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )

    await _dispatch_once()

    connection = Redis.from_url(settings.redis_url)
    workflow_queue = Queue("workflow", connection=connection)
    assert f"workflow-{run.id}" in workflow_queue.job_ids
    job = workflow_queue.fetch_job(f"workflow-{run.id}")
    assert job is not None
    assert job.func_name.endswith("run_workflow")


async def test_redis_unavailable_does_not_lose_the_event_or_touch_the_db_write(monkeypatch):
    """B16 reliability scenario: Redis unavailable during a DB commit. The
    real question isn't whether the domain write itself is protected -
    workflow_service.create_workflow_run (and every other domain write in
    this codebase) only ever writes to Postgres, via the outbox pattern;
    nothing in the request/service path touches Redis at all, structurally,
    by construction (A4-adjacent separation, not something this test needs
    to prove by breaking Redis mid-write - there's no Redis call in that
    path to break). What was genuinely untested reasoning, not verified
    behavior, is _dispatch_once's own try/except around queue.enqueue():
    does a real Redis-unavailable failure there actually leave the outbox
    event retryable, or does it crash the dispatcher / lose the event?

    Verified here for real: point WorkerSettings.redis_url at a port
    nothing listens on (not a mock of the exception - a genuine connection
    failure from the real redis-py client), call _dispatch_once(), and
    confirm (1) it does not raise, (2) the event is marked for retry, not
    dispatched and not silently dropped, (3) the workflow run this event
    describes still exists in Postgres, completely unaffected, since it
    was written before the dispatcher ever touched Redis at all.
    """
    registered, tender = await make_ready_run_inputs(
        "dispatch-redis-down@example.com", "Redis Down Org", "Redis Down Tender"
    )
    run = await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )

    async with UnitOfWork() as uow:
        before_event = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == run.id)
        )
        event_id = before_event.scalar_one().id

    # Port 1 is a real, always-closed port on this host - a genuine
    # connection failure (ConnectionRefusedError, surfaced by redis-py as
    # a real exception on the first command), not a black hole that would
    # instead test a timeout, and not a mock standing in for "some
    # exception happened."
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:1/0")

    await _dispatch_once()  # must not raise

    async with UnitOfWork() as uow:
        event = await uow.session.get(OutboxEvent, event_id)
        run_after = await uow.workflow.get_run(run.id)

    assert event.status == "failed", (
        "a Redis-unavailable enqueue failure must mark the event for retry "
        "(outbox_dispatcher.py's own mark_retry), not leave it looking dispatched"
    )
    assert event.attempt_count == 1
    assert event.dispatched_at is None
    assert run_after is not None and run_after.id == run.id, (
        "the domain write itself must be completely unaffected - it committed to "
        "Postgres before the dispatcher ever tried to reach Redis"
    )


async def test_two_concurrent_dispatchers_claim_each_event_exactly_once(monkeypatch):
    """Major finding #22, the fifth instance of this project's own
    sequential-tested/genuine-concurrency-untested pattern - reproduction
    matters as much as the fix, per instruction, not just a unit-level
    check that claim_by_id's SQL is well-formed. Two real,
    concurrent _dispatch_once() calls (asyncio.gather against real
    Postgres, the same technique that proved every other race in this
    project's own record) against a shared pool of real pending events -
    before the fix (a bare get_pending() batch read, no FOR UPDATE),
    both dispatchers could read and enqueue the SAME event. Tracked here
    by wrapping Queue.enqueue itself (not just checking final event
    status, which is idempotent either way and wouldn't distinguish
    "claimed once" from "claimed twice, both writes landed") - the real
    question is how many times ENQUEUE was actually attempted per event,
    across both concurrent runs combined.
    """
    registered = await auth_service.register(
        email="concurrent-dispatch@example.com",
        password="hunter2",
        organization_name="Concurrent Dispatch Org",
    )
    tenders = [
        await tender_service.create_tender(
            organization_id=registered.organization.id,
            name=f"Concurrent Dispatch Tender {i}",
            created_by=registered.user.id,
        )
        for i in range(10)
    ]

    enqueued_job_ids: list[str] = []
    real_enqueue = Queue.enqueue

    def _tracking_enqueue(self, *args, **kwargs):
        enqueued_job_ids.append(kwargs.get("job_id"))
        return real_enqueue(self, *args, **kwargs)

    monkeypatch.setattr(Queue, "enqueue", _tracking_enqueue)

    await asyncio.gather(_dispatch_once(), _dispatch_once())

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id.in_([t.id for t in tenders]))
        )
        events = list(result.scalars().all())

    assert len(events) == 10
    assert all(e.status == "dispatched" for e in events), (
        "every event must reach a real terminal dispatched state - none silently "
        "lost between two concurrent claimants"
    )

    expected_job_ids = {f"outbox-{e.id}" for e in events}
    assert set(enqueued_job_ids) == expected_job_ids, (
        "every event's job must have been enqueued - none skipped by both "
        "dispatchers simultaneously deciding the other would take it"
    )
    assert len(enqueued_job_ids) == len(expected_job_ids), (
        "each event's job_id must appear in the enqueue log exactly once, not "
        "twice - two concurrent dispatchers claiming and enqueueing the SAME "
        "event is exactly what claim_by_id's FOR UPDATE SKIP LOCKED "
        "exists to prevent; a bare get_pending() read (the pre-fix shape) would "
        "let both dispatchers read and enqueue an overlapping event here"
    )
