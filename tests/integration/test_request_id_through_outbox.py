"""PR 30: one api request's id, through the outbox, to its RQ job.

The api's request id is stored on every outbox event the request writes
(outbox_events.request_id, migration 038), handed by the dispatcher to the
job in its RQ meta, and bound again by the worker (LogContextWorkerMixin),
so the request's lines, the dispatch's and the job's all carry it. Real
Postgres, Redis and the fake LLM provider; the job runs in this process
(SimpleWorker, as in test_orphaned_retries.py: the normal worker forks, and
the dev host is Windows), on a queue of its own, so nothing else in the test
Redis runs.

The end-to-end test is a plain (not async) test: the job's own asyncio.run
cannot start inside a running event loop, so each leg runs in its own
asyncio.run, and the engine's pool is disposed between them (its
connections belong to the loop that made them)."""

import asyncio
import logging
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from neo4j import AsyncGraphDatabase
from neo4j import AsyncSession as Neo4jSession
from redis import Redis
from rq import Queue, SimpleWorker, Worker
from rq.job import Job
from rq.timeouts import TimerDeathPenalty
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from tenderos_api.main import app
from tenderos_backend.core.config import settings as backend_settings
from tenderos_backend.core.log_context import (
    JOB_META_KEY,
    RECORD_ATTRIBUTE,
    current_log_context,
    is_valid_request_id,
    log_context,
)
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import engine, get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_OPERATION_REQUESTED
from tenderos_backend.domain.services import graph_service, operation_service
from tenderos_worker import outbox_dispatcher
from tenderos_worker import worker as worker_module
from tenderos_worker.jobs import operations as operations_job
from tenderos_worker.settings import settings
from tenderos_worker.worker import OPERATIONS_QUEUE, LogContextWorkerMixin

_REQUEST_ID = "cd" * 16  # the W3C shape, low-entropy so it reads as no secret


class _InProcessWorker(LogContextWorkerMixin, SimpleWorker):
    # RQ's default death penalty uses SIGALRM, which Windows does not have.
    death_penalty_class = TimerDeathPenalty


@pytest.fixture
def connection() -> Iterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    client.close()


@pytest.fixture
def operations_queue(connection: Redis) -> Queue:
    """Stands in for the operations queue: the dispatcher looks queues up by
    name, so this one is handed to it under OPERATIONS_QUEUE."""
    return Queue(f"pr30-{uuid.uuid4().hex[:8]}", connection=connection)


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-TenderOS-Client": "web"},
    )


async def _request_red_team(request_id: str | None) -> tuple[str, str]:
    """Register, make a tender, then ask for a red-team run, the last with
    `request_id` as its X-Request-ID. Returns (operation id, the request id
    the response carries)."""
    async with _client() as client:
        registered = await client.post(
            "/auth/register",
            json={
                "email": f"pr30-{uuid.uuid4().hex[:8]}@example.com",
                "password": uuid.uuid4().hex,
                "organization_name": "PR 30 org",
            },
        )
        assert registered.status_code == 201, registered.text
        body = registered.json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        made = await client.post(
            f"/organizations/{body['organization_id']}/tenders",
            json={"name": "PR 30 bid"},
            headers=headers,
        )
        assert made.status_code == 201, made.text
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        started = await client.post(f"/tenders/{made.json()['id']}/redteam/run", headers=headers)
        assert started.status_code == 202, started.text
        return started.json()["operation_id"], started.headers["x-request-id"]


async def _events_for(operation_id: str) -> list[OutboxEvent]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == uuid.UUID(operation_id))
        )
        return list(result.scalars())


async def _requested_event_id(operation_id: str) -> uuid.UUID:
    [event] = [
        e for e in await _events_for(operation_id) if e.event_type == EVENT_OPERATION_REQUESTED
    ]
    return event.id


@asynccontextmanager
async def _neo4j_session_no_pooling() -> AsyncIterator[Neo4jSession]:
    """A new driver for each session, closed with it, in the same loop (as
    tests/route/conftest.py does). The shared driver binds to the first loop
    that used it, and each leg below runs its own: a stale one made the
    red-team job's projection fail with "attached to a different loop" (seen
    in a repeat run), which the job logs and skips. Dropping the shared one
    between legs instead left it unclosed, a ResourceWarning at teardown. On
    the stack each job is a fresh process, so it never has a stale one."""
    driver = AsyncGraphDatabase.driver(
        backend_settings.neo4j_uri,
        auth=(backend_settings.neo4j_user, backend_settings.neo4j_password),
    )
    try:
        async with driver.session(database=backend_settings.neo4j_database) as session:
            yield session
    finally:
        await driver.close()


@pytest.fixture(autouse=True)
def _no_pooling_for_neo4j(monkeypatch):
    # graph_service binds get_session under its own name at import, so that
    # name is the one to patch (tests/route/conftest.py's note).
    monkeypatch.setattr(graph_service, "get_neo4j_session", _neo4j_session_no_pooling)


def _fresh_pools() -> None:
    """Empty the SQL engine's pool: its connections belong to the event loop
    that made them (the autouse cleanup fixture's is pytest's), and each leg
    below runs its own loop."""
    asyncio.run(engine.dispose())


def _run(coro):
    """One leg in its own event loop, with fresh pools before and after."""
    _fresh_pools()
    try:
        return asyncio.run(coro)
    finally:
        _fresh_pools()


@pytest.mark.asyncio
async def test_every_event_a_request_writes_stores_its_request_id():
    operation_id, request_id = await _request_red_team(_REQUEST_ID)
    assert request_id == _REQUEST_ID
    events = await _events_for(operation_id)
    # OperationRequested and the queued OperationStatusChanged, one transaction.
    assert len(events) == 2
    assert {e.request_id for e in events} == {_REQUEST_ID}


@pytest.mark.asyncio
async def test_a_request_without_one_stores_the_id_it_was_given():
    operation_id, request_id = await _request_red_team(None)
    assert is_valid_request_id(request_id)
    assert {e.request_id for e in await _events_for(operation_id)} == {request_id}


@pytest.mark.asyncio
async def test_an_event_written_outside_a_request_has_no_request_id():
    async with get_session() as session:
        org = Organization(name="PR 30 no request org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="PR 30 no request bid")
        session.add(tender)
        await session.commit()
        org_id, tender_id = org.id, tender.id
    requested = await operation_service.request_operation(org_id, tender_id, "redteam_run", None)
    events = await _events_for(str(requested.operation.id))
    assert events and {e.request_id for e in events} == {None}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["NOT-HEX", "A" * 32, "ab" * 15])
async def test_the_database_refuses_any_other_request_id(value):
    operation_id, _ = await _request_red_team(_REQUEST_ID)
    event_id = await _requested_event_id(operation_id)
    async with UnitOfWork() as uow:
        with pytest.raises(IntegrityError, match="ck_outbox_request_id"):
            await uow.session.execute(
                text("UPDATE outbox_events SET request_id = :value WHERE id = :id"),
                {"value": value, "id": event_id},
            )


def test_the_dispatcher_hands_the_ids_to_the_job_in_its_meta(operations_queue, connection):
    operation_id, _ = _run(_request_red_team(_REQUEST_ID))
    event_id = _run(_requested_event_id(operation_id))
    _run(outbox_dispatcher._dispatch_claimed_event({OPERATIONS_QUEUE: operations_queue}, event_id))
    job = Job.fetch(outbox_dispatcher.operation_job_id(operation_id), connection=connection)
    assert job.meta == {JOB_META_KEY: {"request_id": _REQUEST_ID, "operation_id": operation_id}}


@pytest.mark.asyncio
async def test_dispatching_an_event_leaves_the_callers_ids_as_they_were(operations_queue):
    """The dispatcher binds each event's ids for that event only
    (log_context_scope): its tick's next lines, and the next event's, do not
    carry them."""
    operation_id, _ = await _request_red_team(_REQUEST_ID)
    event_id = await _requested_event_id(operation_id)
    with log_context(job_id="dispatcher_tick"):
        before = current_log_context()
        await outbox_dispatcher._dispatch_claimed_event(
            {OPERATIONS_QUEUE: operations_queue}, event_id
        )
        assert current_log_context() == before


def test_the_worker_process_runs_its_jobs_on_the_binding_worker(monkeypatch):
    """worker.main() builds LogContextWorker, RQ's forking Worker with the
    job's ids bound; a plain Worker would run every job with none. Both are
    replaced here by stand-ins that stop main() before it listens."""

    class Built(Exception):
        pass

    def stand_in(name: str):
        def build(*args, **kwargs):
            raise Built(name)

        return build

    assert issubclass(worker_module.LogContextWorker, Worker)
    assert issubclass(worker_module.LogContextWorker, LogContextWorkerMixin)
    monkeypatch.setattr(worker_module, "LogContextWorker", stand_in("LogContextWorker"))
    monkeypatch.setattr(worker_module, "Worker", stand_in("Worker"))
    monkeypatch.setattr(sys, "argv", ["worker", "workflow"])
    with pytest.raises(Built, match="^LogContextWorker$"):
        worker_module.main()


def _carries(record: logging.LogRecord, **ids: str) -> bool:
    context = getattr(record, RECORD_ATTRIBUTE, {})
    return all(context.get(name) == value for name, value in ids.items())


def test_one_requests_lines_share_its_request_id_from_the_api_to_the_job(
    operations_queue, connection, caplog
):
    """The request's own line (the operation requested), the dispatcher's
    (the event enqueued), and every line written while the job ran (its
    first line and RQ's own line that it finished among them) carry the
    request id; the dispatcher's and the job's carry the operation and job
    ids too. RQ's line naming the job as it is dequeued comes before the
    worker binds anything (worker.LogContextWorkerMixin), so it is not
    among them."""
    caplog.set_level(logging.INFO)
    operation_id, _ = _run(_request_red_team(_REQUEST_ID))
    event_id = _run(_requested_event_id(operation_id))
    _run(outbox_dispatcher._dispatch_claimed_event({OPERATIONS_QUEUE: operations_queue}, event_id))
    worker = _InProcessWorker([operations_queue], connection=connection)
    _fresh_pools()
    worker.work(burst=True, with_scheduler=False)
    _fresh_pools()

    job_id = outbox_dispatcher.operation_job_id(operation_id)
    records = caplog.records

    def one(logger: str, starts: str) -> logging.LogRecord:
        found = [r for r in records if r.name == logger and r.getMessage().startswith(starts)]
        assert len(found) == 1, [r.getMessage() for r in records if r.name == logger]
        return found[0]

    requested = one(operation_service.__name__, f"operation {operation_id} requested")
    enqueued = one(outbox_dispatcher.__name__, f"outbox event {event_id} (")
    job_started = one("tenderos_worker.jobs.operations", f"operation {operation_id}: job code")
    finished = one("rq.worker", "Successfully completed tenderos_worker.jobs.operations")

    assert _carries(requested, request_id=_REQUEST_ID)
    assert _carries(enqueued, request_id=_REQUEST_ID, operation_id=operation_id, job_id=job_id)
    for record in (job_started, finished):
        assert _carries(record, request_id=_REQUEST_ID, operation_id=operation_id, job_id=job_id)

    # Every line written from the job's first line to RQ's last one, taken by
    # position in the capture (the order they were made), not by timestamp:
    # Windows' clock ticks about every 16 ms, so RQ's "Listening on" line,
    # written before the job and so with no ids, could share the job's first
    # line's timestamp (seen twice in ten repeat runs).
    first, last = records.index(job_started), records.index(finished)
    during_job = records[first : last + 1]
    assert len(during_job) >= 2
    for record in during_job:
        assert _carries(record, request_id=_REQUEST_ID, operation_id=operation_id, job_id=job_id), (
            record.name,
            record.getMessage(),
            getattr(record, RECORD_ATTRIBUTE, None),
        )


def test_the_operations_job_adds_its_tender_and_organization(monkeypatch):
    """Its OperationRequested payload names neither, so run_operation binds
    them once it has read the operation."""
    operation_id, _ = _run(_request_red_team(_REQUEST_ID))
    seen: list[dict[str, str]] = []

    async def recording_handler(operation) -> None:
        seen.append(current_log_context())

    monkeypatch.setitem(operations_job._HANDLERS, "redteam_run", recording_handler)
    _run(operations_job.run_operation(operation_id))
    operation = _run(operation_service.get_operation(uuid.UUID(operation_id)))
    assert operation is not None
    assert seen == [
        {"tender_id": str(operation.tender_id), "org_id": str(operation.organization_id)}
    ]
