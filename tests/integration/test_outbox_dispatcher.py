import asyncio
import contextlib
import logging
import socket
import threading
import uuid
from datetime import datetime, timedelta

import pytest
from _helpers import make_ready_run_inputs
from redis import Redis
from rq import Queue
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.idempotency import IdempotencyRepository
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    auth_service,
    document_service,
    knowledge_service,
    operation_service,
    tender_service,
    workflow_service,
)
from tenderos_worker import outbox_dispatcher
from tenderos_worker.jobs.ingestion import PARSER_VERSION
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
    must land on the "workflow" queue running run_workflow_job, not
    "maintenance" running the generic process_outbox_event placeholder every
    other event type still falls back to. run_workflow_job (jobs/workflow.py),
    not the bare run_workflow - B17's own fix for RQ's death-penalty timeout
    bypassing run_workflow's except block requires RQ to call OUR sync
    wrapper directly, not the async function (see _run_sync's docstring)."""
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
    assert job.func_name.endswith("run_workflow_job")


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
    # exception happened." 127.0.0.1 rather than localhost: on this host a
    # refused connect costs about 2 s per address family (KNOWN_LIMITATIONS,
    # the test_outbox_dispatcher.py flake entry).
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    async with UnitOfWork() as uow:
        available_before = (await uow.session.get(OutboxEvent, event_id)).available_at

    # Phase 4 blocker 3: Redis being unreachable no longer consumes an
    # attempt. The tick fails fast (RedisUnavailableError, which
    # _run_forever turns into its tick backoff) and the row is left exactly
    # as it was.
    with pytest.raises(outbox_dispatcher.RedisUnavailableError):
        await _dispatch_once()

    async with UnitOfWork() as uow:
        event = await uow.session.get(OutboxEvent, event_id)
        run_after = await uow.workflow.get_run(run.id)

    assert event.status == "pending", (
        "a Redis-unavailable enqueue must leave the event pending for a later "
        "tick, not count it as a failed attempt"
    )
    assert event.attempt_count == 0
    assert event.available_at == available_before
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


# ---------------------------------------------------------------------------
# Phase 4 blocker 3: database clock, bounded Redis calls, unique enqueue, and
# a loop that survives failures.
# ---------------------------------------------------------------------------

_LOOP_WAIT_SECONDS = 20


async def _a_pending_event(email: str) -> OutboxEvent:
    """A TenderCreated event, which routes to the maintenance queue as
    job outbox-{event.id} (outbox_dispatcher._default_dispatch)."""
    registered = await auth_service.register(
        email=email, password="hunter2", organization_name=f"{email} org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name=f"{email} tender",
        created_by=registered.user.id,
    )
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == tender.id)
        )
        return result.scalar_one()


async def _event(event_id) -> OutboxEvent:
    async with UnitOfWork() as uow:
        event = await uow.session.get(OutboxEvent, event_id)
    assert event is not None
    return event


async def _run_loop_until(condition, *, timeout: float = _LOOP_WAIT_SECONDS) -> None:
    """Run the real _run_forever until `condition()` is true, then stop it.
    Fails if the loop ends on its own (it must never return) or the
    condition is not met in time."""
    loop_task = asyncio.create_task(outbox_dispatcher._run_forever())
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while not condition():
            assert not loop_task.done(), f"the loop stopped: {loop_task.exception()!r}"
            assert asyncio.get_running_loop().time() < deadline, "condition not met in time"
            await asyncio.sleep(0.02)
        assert not loop_task.done(), "the loop must still be running"
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task


class _HostClockBehind(datetime):
    """The API/dispatcher host's clock, 30 s behind the database's."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) - timedelta(seconds=30)


async def test_eligibility_uses_the_database_clock(monkeypatch):
    """available_at is stamped by Postgres on insert. With the host clock
    30 s behind, a host-clock comparison would hide a brand-new event for
    30 s; a database-clock comparison finds it at once. raising=False: once
    no module reads the host clock any more, these patches have nothing to
    replace, which is the point."""
    from tenderos_backend.db.repositories import outbox as outbox_repository

    monkeypatch.setattr(outbox_repository, "datetime", _HostClockBehind, raising=False)
    monkeypatch.setattr(outbox_dispatcher, "datetime", _HostClockBehind, raising=False)
    event = await _a_pending_event("clock-behind@example.com")

    await _dispatch_once()

    after = await _event(event.id)
    assert after.status == "dispatched", (after.status, after.available_at)
    assert after.dispatched_at is not None


@contextlib.contextmanager
def _black_hole_redis():
    """A TCP server that accepts connections and never answers: what a hung
    Redis looks like to a client. Yields its redis:// URL."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    server.settimeout(0.2)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept_and_hold():
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                held.append(conn)
            except OSError:
                continue

    thread = threading.Thread(target=accept_and_hold, daemon=True)
    thread.start()
    try:
        yield f"redis://127.0.0.1:{server.getsockname()[1]}/0"
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in held:
            conn.close()
        server.close()


async def test_a_hung_redis_enqueue_times_out_and_the_loop_keeps_running(monkeypatch):
    """Before: no socket timeout of our own (redis-py's 5 s default per
    command) and a synchronous enqueue on the event loop. Now the enqueue
    gives up after the socket timeout, which is an infrastructure error:
    the event stays pending with attempt_count 0, and the real loop goes on
    to its next tick on its tick backoff."""
    event = await _a_pending_event("hung-redis@example.com")
    monkeypatch.setattr(outbox_dispatcher, "REDIS_SOCKET_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(outbox_dispatcher, "REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", 0.05)

    ticks: list[float] = []
    real_dispatch_once = outbox_dispatcher._dispatch_once

    async def timed_dispatch_once(queues=None):
        started = asyncio.get_running_loop().time()
        try:
            await real_dispatch_once(queues)
        finally:
            ticks.append(asyncio.get_running_loop().time() - started)

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", timed_dispatch_once)

    with _black_hole_redis() as url:
        monkeypatch.setattr(settings, "redis_url", url)
        await _run_loop_until(lambda: len(ticks) >= 2)

    after = await _event(event.id)
    assert after.status == "pending"
    assert after.attempt_count == 0
    assert after.dispatched_at is None
    # Bounded by the socket timeout, not redis-py's 5 s default or forever.
    assert ticks[0] < 5, ticks


async def test_an_existing_job_id_is_treated_as_already_dispatched(monkeypatch):
    """The crash this guards against: the enqueue reached Redis, then the
    dispatcher died before marking the row, so the row is still pending.
    Simulated by making mark_dispatched fail once after a real enqueue. The
    next tick finds the job already in Redis (unique=True raises
    DuplicateJobError) and marks the event dispatched without a second
    job."""
    event = await _a_pending_event("dup-job@example.com")
    job_id = f"outbox-{event.id}"

    real_mark = OutboxRepository.mark_dispatched
    crashed = {"done": False}

    async def crash_once(self, event_id):
        if not crashed["done"]:
            crashed["done"] = True
            raise RuntimeError("simulated crash after the enqueue, before the mark")
        return await real_mark(self, event_id)

    monkeypatch.setattr(OutboxRepository, "mark_dispatched", crash_once)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await _dispatch_once()
    assert (await _event(event.id)).status == "pending"

    await _dispatch_once()

    after = await _event(event.id)
    assert after.status == "dispatched"
    connection = Redis.from_url(settings.redis_url)
    try:
        maintenance = Queue(outbox_dispatcher.DEFAULT_QUEUE_NAME, connection=connection)
        assert maintenance.job_ids.count(job_id) == 1
    finally:
        connection.close()


async def test_the_loop_survives_a_database_error_in_a_tick(monkeypatch):
    """A tick that raises (Postgres unreachable, here simulated) is logged
    and retried after a backoff; it no longer ends the process."""
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", 0.01)
    calls: list[int] = []

    async def failing_then_fine(queues=None):
        calls.append(1)
        if len(calls) <= 2:
            raise OperationalError("SELECT 1", {}, Exception("database unreachable"))

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", failing_then_fine)

    await _run_loop_until(lambda: len(calls) >= 4)


async def test_a_cleanup_failure_does_not_stop_the_loop(monkeypatch):
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(outbox_dispatcher, "CLEANUP_EVERY_N_TICKS", 1)
    cleanup_calls: list[int] = []

    async def no_events(queues=None):
        return None

    async def broken_cleanup(self, batch_size):
        cleanup_calls.append(batch_size)
        raise RuntimeError("cleanup broke")

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", no_events)
    monkeypatch.setattr(IdempotencyRepository, "delete_expired", broken_cleanup)

    await _run_loop_until(lambda: len(cleanup_calls) >= 3)
    assert cleanup_calls[0] == outbox_dispatcher.CLEANUP_BATCH_SIZE


# ---------------------------------------------------------------------------
# Phase 4 blocker 3 retry policy: Redis being unreachable never consumes an
# attempt; any other enqueue error does, with growing, capped delays, and
# the last one dead-letters the event.
# ---------------------------------------------------------------------------

_DEAD_PORT_URL = "redis://127.0.0.1:1/0"


async def test_an_event_written_during_a_long_outage_is_dispatched_when_redis_returns(
    monkeypatch,
):
    """The live failure this replaces: during a 22 s outage, an event used
    up its 3 attempts (1, 2 and 4 s apart) and was never delivered. Here
    Redis is unreachable for more ticks than the old policy allowed
    attempts; the event keeps attempt_count 0 throughout and is dispatched
    on the first tick after Redis is back."""
    event = await _a_pending_event("long-outage@example.com")
    real_url = settings.redis_url
    monkeypatch.setattr(settings, "redis_url", _DEAD_PORT_URL)

    for _ in range(4):
        with pytest.raises(outbox_dispatcher.RedisUnavailableError):
            await _dispatch_once()
        during = await _event(event.id)
        assert (during.status, during.attempt_count) == ("pending", 0)

    monkeypatch.setattr(settings, "redis_url", real_url)
    await _dispatch_once()

    after = await _event(event.id)
    assert after.status == "dispatched"
    assert after.attempt_count == 0


async def test_a_connection_error_stops_the_tick_after_that_row(monkeypatch):
    """Fail fast: with Redis unreachable, the first row's enqueue fails and
    the rest of the batch is not attempted (each would cost a full
    timeout). Every event is left pending with attempt_count 0."""
    events = [await _a_pending_event(f"fail-fast-{i}@example.com") for i in range(3)]
    enqueue_attempts: list[str] = []
    real_enqueue = Queue.enqueue

    def counting_enqueue(self, *args, **kwargs):
        enqueue_attempts.append(kwargs.get("job_id"))
        return real_enqueue(self, *args, **kwargs)

    monkeypatch.setattr(Queue, "enqueue", counting_enqueue)
    monkeypatch.setattr(settings, "redis_url", _DEAD_PORT_URL)

    with pytest.raises(outbox_dispatcher.RedisUnavailableError):
        await _dispatch_once()

    assert len(enqueue_attempts) == 1
    for event in events:
        after = await _event(event.id)
        assert (after.status, after.attempt_count) == ("pending", 0)


async def test_other_enqueue_errors_consume_attempts_with_growing_capped_delays(
    monkeypatch, caplog: pytest.LogCaptureFixture
):
    """A non-infrastructure error (here a ValueError from the enqueue, the
    kind a bad job definition raises) consumes an attempt each time. The
    delay before the next attempt grows from the base and stops at the cap;
    the last attempt marks the event failed (the dead-letter state) and
    logs it at ERROR with its id and event type. Jitter is pinned to its
    upper bound so the delays are exact; the event is made eligible again
    between attempts instead of waiting out each delay."""
    monkeypatch.setattr(logging.getLogger("tenderos_worker.outbox_dispatcher"), "disabled", False)
    caplog.set_level(logging.ERROR, logger="tenderos_worker.outbox_dispatcher")
    monkeypatch.setattr(outbox_dispatcher.random, "uniform", lambda low, high: high)

    def broken_enqueue(self, *args, **kwargs):
        raise ValueError("this job cannot be enqueued")

    monkeypatch.setattr(Queue, "enqueue", broken_enqueue)
    event = await _a_pending_event("dead-letter@example.com")

    delays: list[float] = []
    for attempt in range(1, outbox_dispatcher.MAX_ATTEMPTS + 1):
        await _dispatch_once()
        async with UnitOfWork() as uow:
            row = (
                await uow.session.execute(
                    text(
                        "SELECT status, attempt_count, "
                        "extract(epoch FROM available_at - now()) AS wait "
                        "FROM outbox_events WHERE id = :id"
                    ),
                    {"id": event.id},
                )
            ).one()
            assert row.attempt_count == attempt
            if attempt < outbox_dispatcher.MAX_ATTEMPTS:
                assert row.status == "pending"
                delays.append(float(row.wait))
                await uow.session.execute(
                    text("UPDATE outbox_events SET available_at = now() WHERE id = :id"),
                    {"id": event.id},
                )
                await uow.commit()
            else:
                assert row.status == "failed"

    expected = [30.0, 60.0, 120.0, 240.0, 300.0, 300.0, 300.0]
    assert len(delays) == len(expected)
    for got, want in zip(delays, expected, strict=True):
        assert abs(got - want) <= 2, (delays, expected)
    assert max(delays) <= outbox_dispatcher.RETRY_CAP_SECONDS + 2

    errors = [
        r
        for r in caplog.records
        if r.levelno == logging.ERROR and "marked failed" in r.getMessage()
    ]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert str(event.id) in message
    assert event.event_type in message

    # Dead-lettered: never selected again.
    await _dispatch_once()
    final = await _event(event.id)
    assert (final.status, final.attempt_count) == ("failed", outbox_dispatcher.MAX_ATTEMPTS)


# ---------------------------------------------------------------------------
# Job ids identify exactly one event. With unique=True, a job id shared by
# two events made the second one "already dispatched" while the first job's
# hash still existed in Redis (500 s after it finished, a year after it
# failed), so the second event's work never ran.
# ---------------------------------------------------------------------------


def _job_ids(queue_name: str, needle: str) -> list[str]:
    connection = Redis.from_url(settings.redis_url)
    try:
        return [j for j in Queue(queue_name, connection=connection).job_ids if needle in j]
    finally:
        connection.close()


async def test_two_document_uploaded_events_for_one_document_enqueue_two_jobs():
    """An upload and a later explicit reprocess of the same document are two
    DocumentUploaded events; each must get its own ingest job."""
    registered = await auth_service.register(
        email="two-ingests@example.com", password="hunter2", organization_name="Two Ingests Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="Two Ingests Tender",
        created_by=registered.user.id,
    )
    document, _ = await document_service.create_document(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        actor_user_id=registered.user.id,
        filename="two-ingests.docx",
        content=b"not parsed in this test",
        mime_type=None,
    )
    await _dispatch_once()
    await document_service.reprocess_document(
        document.id, registered.organization.id, registered.user.id
    )
    await _dispatch_once()

    jobs = _job_ids("ingestion", str(document.id))
    assert len(jobs) == 2, jobs
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == document.id,
                OutboxEvent.event_type == "DocumentUploaded",
            )
        )
        events = result.scalars().all()
    assert len(events) == 2
    assert all(e.status == "dispatched" for e in events)
    assert {f"ingest_{document.id}_{PARSER_VERSION}_{e.id}" for e in events} == set(jobs)


async def test_two_knowledge_imports_for_one_document_enqueue_two_jobs():
    """replace_employee_resume can re-queue an existing document (content
    dedup reused its row) under a fresh request id: a second
    KnowledgeDocumentUploaded event for the same document, with its own
    batch. Each must get its own import job."""
    document_id = uuid.uuid4()
    await knowledge_service.queue_knowledge_document_import(document_id, uuid.uuid4())
    await _dispatch_once()
    await knowledge_service.queue_knowledge_document_import(
        document_id, uuid.uuid4(), request_id=uuid.uuid4()
    )
    await _dispatch_once()

    jobs = _job_ids("workflow", f"knowledge_import_{document_id}")
    assert len(jobs) == 2, jobs
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == document_id)
        )
        events = result.scalars().all()
    assert len(events) == 2
    assert all(e.status == "dispatched" for e in events)
    assert {f"knowledge_import_{document_id}_{e.id}" for e in events} == set(jobs)


# ---------------------------------------------------------------------------
# Phase 4: async operations. OperationRequested is routed to the worker's
# run_operation_job under a job id naming the operation, with the job
# timeout of the operation's kind; the reaper runs from the loop.
# ---------------------------------------------------------------------------


async def test_operation_requested_routes_to_run_operation_with_its_kinds_timeout():
    registered = await auth_service.register(
        email="dispatch-op@example.com", password="hunter2", organization_name="Dispatch Op Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="Dispatch Op Tender",
        created_by=registered.user.id,
    )
    requested = await operation_service.request_operation(
        registered.organization.id, tender.id, "redteam_run", registered.user.id
    )

    await _dispatch_once()

    connection = Redis.from_url(settings.redis_url)
    try:
        job = Queue("workflow", connection=connection).fetch_job(f"op_{requested.operation.id}")
        assert job is not None
        assert job.func_name.endswith("run_operation_job")
        assert job.args == (str(requested.operation.id),)
        assert job.timeout == operation_service.JOB_TIMEOUT_SECONDS["redteam_run"]
    finally:
        connection.close()


async def test_a_reaper_failure_is_logged_and_does_not_escape(monkeypatch, caplog):
    async def broken(batch_size: int):
        raise OperationalError("fake", {}, Exception("fake database outage"))

    monkeypatch.setattr(operation_service, "reap_lapsed_operations", broken)

    with caplog.at_level(logging.WARNING, logger=outbox_dispatcher.logger.name):
        await outbox_dispatcher._reap_lapsed_operations()

    assert any("reap" in r.getMessage().lower() for r in caplog.records)
