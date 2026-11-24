"""Plan item 105 (PR 33): a red-team operation that ends failed without a
pass row gets a failed pass, so readiness stops showing the previous pass's
reason. Two endings wrote none before:

- RQ's job timeout landing while the event loop waits: it surfaces outside
  the pass and asyncio.run cancels it (run_red_team's own except never
  runs). Recovered by run_operation_job's timeout handling. A timeout during
  the projection step, before the pass starts, counts too (user decision).
- A lost worker: the dispatcher's reaper fails the operation as WORKER_LOST.

Both go through operation_service.record_failed_redteam_pass. The timeout
tests run a real RQ worker in this process. Its death penalty is
TimerDeathPenalty, as in test_orphaned_retries.py: Windows has no SIGALRM, and
like the signal it raises JobTimeoutException asynchronously in the main
thread, so it lands in the event loop's own code after its wait returns
(the case run_red_team cannot see), not inside the handler.
"""

import asyncio
import uuid
from collections.abc import Iterator

import pytest
from _helpers import register_org_and_tender
from redis import Redis
from rq import Queue, SimpleWorker
from rq.job import Job, JobStatus
from rq.timeouts import JobTimeoutException, TimerDeathPenalty
from sqlalchemy import select, update
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.redteam import RedteamPass
from tenderos_backend.db.session import engine
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, operation_service, redteam_service
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs.operations import run_operation_job
from tenderos_worker.settings import settings

# The job's timeout, and how long the stand-in work waits: the timeout lands
# while the event loop waits on that sleep.
_JOB_TIMEOUT_SECONDS = 1
_WORK_SECONDS = 3


class _InProcessWorker(SimpleWorker):
    death_penalty_class = TimerDeathPenalty


@pytest.fixture
def connection() -> Iterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    client.close()


@pytest.fixture
def queue(connection: Redis) -> Queue:
    return Queue(f"item105-{uuid.uuid4().hex[:8]}", connection=connection)


def _drop_pool() -> None:
    """Forget the shared engine's pooled connections without closing them:
    they may belong to an earlier test's event loop, which closing them from
    this one would touch."""
    engine.sync_engine.dispose(close=False)


def _sync(coro):
    """Run setup or a read in a fresh loop, leaving the shared engine's pool
    empty for the job's own asyncio.run (as test_orphaned_retries.py)."""
    _drop_pool()

    async def _run():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_run())


async def _request(email: str, kind: str) -> uuid.UUID:
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    requested = await operation_service.request_operation(
        registered.organization.id, tender.id, kind, registered.user.id
    )
    return requested.operation.id


async def _passes(operation_id: uuid.UUID) -> list[tuple[str, str | None]]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(RedteamPass.outcome, RedteamPass.error_code).where(
                RedteamPass.operation_id == operation_id
            )
        )
        return [(row.outcome, row.error_code) for row in result]


async def _operation(operation_id: uuid.UUID) -> AsyncOperation:
    operation = await operation_service.get_operation(operation_id)
    assert operation is not None
    return operation


def _run_job_past_its_timeout(
    queue: Queue,
    connection: Redis,
    operation_id: uuid.UUID,
    job_timeout: int = _JOB_TIMEOUT_SECONDS,
) -> Job:
    job = queue.enqueue(run_operation_job, str(operation_id), job_timeout=job_timeout)
    _sync(engine.dispose())
    _InProcessWorker([queue], connection=connection).work(burst=True, with_scheduler=False)
    _sync(engine.dispose())
    return job


async def _waits(operation: AsyncOperation) -> None:
    await asyncio.sleep(_WORK_SECONDS)


def test_a_timeout_while_the_loop_waits_records_a_failed_pass(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """The case item 105 is about: before, the operation ended JOB_TIMEOUT
    with no pass row."""
    monkeypatch.setitem(operation_jobs._HANDLERS, "redteam_run", _waits)
    operation_id = _sync(_request("item105-wait@example.com", "redteam_run"))

    _run_job_past_its_timeout(queue, connection, operation_id)

    operation = _sync(_operation(operation_id))
    assert (operation.status, operation.error_code) == ("failed", "JOB_TIMEOUT")
    assert _sync(_passes(operation_id)) == [("failed", "JOB_TIMEOUT")]


def test_a_timeout_during_the_projection_step_records_a_failed_pass(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """The real handler: the timeout lands while _project_if_stale waits on
    the graph, before run_red_team starts (user decision: it counts)."""

    async def slow_status(tender_id: uuid.UUID) -> dict:
        await asyncio.sleep(_WORK_SECONDS)
        raise AssertionError("the job should have timed out")

    monkeypatch.setattr(graph_service, "get_graph_status", slow_status)
    operation_id = _sync(_request("item105-project@example.com", "redteam_run"))

    _run_job_past_its_timeout(queue, connection, operation_id)

    assert _sync(_operation(operation_id)).error_code == "JOB_TIMEOUT"
    assert _sync(_passes(operation_id)) == [("failed", "JOB_TIMEOUT")]


def test_a_timeout_inside_the_pass_ends_job_timeout_with_one_failed_pass(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """Landing inside the pass's own code, run_red_team records the failed
    pass (its type name as the code) and re-raises. run_operation lets the
    timeout through (PR 33, user decision; before, its `except Exception`
    failed the operation as INTERNAL_ERROR), so the recovery fails it as
    JOB_TIMEOUT, finds the pass already recorded and writes no second row,
    and RQ files the job as failed."""

    async def times_out_inside(*args, **kwargs):
        raise JobTimeoutException("fake: the timeout landed in the pass's own code")

    monkeypatch.setattr(redteam_service, "_run_and_record_pass", times_out_inside)
    monkeypatch.setattr(operation_jobs, "_project_if_stale", _no_projection)
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: None)
    operation_id = _sync(_request("item105-inside@example.com", "redteam_run"))

    job = _run_job_past_its_timeout(queue, connection, operation_id, job_timeout=600)

    operation = _sync(_operation(operation_id))
    assert (operation.status, operation.error_code) == ("failed", "JOB_TIMEOUT")
    assert _sync(_passes(operation_id)) == [("failed", "JobTimeoutException")]
    assert job.get_status(refresh=True) == JobStatus.FAILED


def test_a_timeout_inside_another_kinds_own_code_ends_job_timeout(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """Any operation kind: a timeout raised from the work's own code (not
    while the loop waits) ends as JOB_TIMEOUT through the recovery, not
    INTERNAL_ERROR, and RQ files the job as failed. No red-team pass."""

    async def times_out_inside(operation: AsyncOperation) -> None:
        raise JobTimeoutException("fake: the timeout landed in the work's own code")

    monkeypatch.setitem(operation_jobs._HANDLERS, "proposal_generation", times_out_inside)
    operation_id = _sync(_request("item105-inside-pg@example.com", "proposal_generation"))

    job = _run_job_past_its_timeout(queue, connection, operation_id, job_timeout=600)

    operation = _sync(_operation(operation_id))
    assert (operation.status, operation.error_code) == ("failed", "JOB_TIMEOUT")
    assert _sync(_passes(operation_id)) == []
    assert job.get_status(refresh=True) == JobStatus.FAILED


async def _no_projection(tender_id: uuid.UUID) -> None:
    return None


def test_a_timed_out_operation_of_another_kind_records_no_pass(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setitem(operation_jobs._HANDLERS, "proposal_generation", _waits)
    operation_id = _sync(_request("item105-proposal@example.com", "proposal_generation"))

    _run_job_past_its_timeout(queue, connection, operation_id)

    assert _sync(_operation(operation_id)).error_code == "JOB_TIMEOUT"
    assert _sync(_passes(operation_id)) == []


def test_the_recovery_records_nothing_for_an_operation_it_did_not_fail(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """Someone else failed the operation while the job ran (here a direct
    update standing in for another path that records no pass). The
    recovery's own fail finds it no longer running, so it writes nothing:
    the pass belongs to whoever ended it."""

    async def failed_elsewhere_then_waits(operation: AsyncOperation) -> None:
        async with UnitOfWork() as uow:
            await uow.session.execute(
                update(AsyncOperation)
                .where(AsyncOperation.id == operation.id)
                .values(status="failed", error_code="WORKER_LOST")
            )
            await uow.commit()
        await asyncio.sleep(_WORK_SECONDS)

    monkeypatch.setitem(operation_jobs._HANDLERS, "redteam_run", failed_elsewhere_then_waits)
    operation_id = _sync(_request("item105-other@example.com", "redteam_run"))

    _run_job_past_its_timeout(queue, connection, operation_id)

    assert _sync(_operation(operation_id)).error_code == "WORKER_LOST"
    assert _sync(_passes(operation_id)) == []


def test_the_recovery_records_nothing_when_its_own_fail_loses_the_claim(
    queue: Queue, connection: Redis, monkeypatch: pytest.MonkeyPatch
):
    """The race the recovery's own guard is for: the operation still read
    running, then another path failed it (here one that records no pass)
    before the recovery's fail, which finds its claim gone. The recovery
    writes nothing; the pass belongs to whoever ended it."""
    real_finish = operation_service.finish_operation

    async def claim_lost(operation_id, claim_token, status, error_code=None, error_message=None):
        if status != "failed":
            return await real_finish(operation_id, claim_token, status, error_code, error_message)
        async with UnitOfWork() as uow:
            await uow.session.execute(
                update(AsyncOperation)
                .where(AsyncOperation.id == operation_id)
                .values(status="failed", error_code="WORKER_LOST")
            )
            await uow.commit()
        return False

    monkeypatch.setitem(operation_jobs._HANDLERS, "redteam_run", _waits)
    monkeypatch.setattr(operation_service, "finish_operation", claim_lost)
    operation_id = _sync(_request("item105-lost-claim@example.com", "redteam_run"))

    _run_job_past_its_timeout(queue, connection, operation_id)

    assert _sync(_operation(operation_id)).error_code == "WORKER_LOST"
    assert _sync(_passes(operation_id)) == []


@pytest.mark.asyncio
async def test_the_reaper_records_a_failed_pass_for_a_lost_red_team_operation():
    """A lost worker: the reaper fails the operation as WORKER_LOST and, in
    the same transaction, records its failed pass, so readiness reads it as
    the newest. Another kind reaped alongside gets none."""
    redteam_id = await _request("item105-reap-rt@example.com", "redteam_run")
    proposal_id = await _request("item105-reap-pg@example.com", "proposal_generation")
    for operation_id, kind in ((redteam_id, "redteam_run"), (proposal_id, "proposal_generation")):
        claimed = await operation_service.claim_operation(
            operation_id, operation_service.lease_for(kind)
        )
        assert claimed is not None
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id.in_([redteam_id, proposal_id]))
            .values(locked_until=AsyncOperation.started_at - operation_service.LEASE_MARGIN)
        )
        await uow.commit()

    reaped = await operation_service.reap_lapsed_operations(batch_size=100)

    assert sorted(reaped) == sorted([redteam_id, proposal_id])
    assert await _passes(redteam_id) == [("failed", "WORKER_LOST")]
    assert await _passes(proposal_id) == []
    tender_id = (await _operation(redteam_id)).tender_id
    async with UnitOfWork() as uow:
        latest = await uow.redteam.latest_pass(tender_id)
    assert latest is not None and latest.operation_id == redteam_id
    # A second reap finds nothing, and writes no second row.
    assert await operation_service.reap_lapsed_operations(batch_size=100) == []
    assert await _passes(redteam_id) == [("failed", "WORKER_LOST")]


@pytest.mark.asyncio
async def test_the_helper_leaves_a_running_operation_alone():
    operation_id = await _request("item105-running@example.com", "redteam_run")
    claimed = await operation_service.claim_operation(
        operation_id, operation_service.lease_for("redteam_run")
    )
    assert claimed is not None

    assert await operation_service.record_failed_redteam_pass_after_timeout(operation_id) is False
    assert await _passes(operation_id) == []
