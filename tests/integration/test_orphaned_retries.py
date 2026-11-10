"""Phase 4 plan item 22: orphaned scheduled retries. A retry is orphaned if
its run's row is gone, its run is not running, or its run's current job is
not this retry; age alone never counts (test_run_reaper.py covers the two
guards item 19 added). Real Postgres and Redis, RQ's real scheduler and a real
worker loop. SimpleWorker runs the job in this process, since the normal
worker forks and the dev host is Windows; each test has its own queue, so
nothing else in the test Redis runs or is counted."""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from rq.scheduler import RQScheduler
from rq.timeouts import TimerDeathPenalty
from tenderos_backend.agents.llm.client import RateLimitedError
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import engine, get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import run_reaper
from tenderos_backend.domain.services.workflow_service import retry_job_id, start_job_id
from tenderos_worker.jobs import workflow as workflow_jobs
from tenderos_worker.settings import settings

_RETRY_JOB = "tenderos_worker.jobs.workflow.retry_workflow_run_job"
_LOGGER = workflow_jobs.__name__


class _InProcessWorker(SimpleWorker):
    # RQ's default death penalty uses SIGALRM, which Windows does not have.
    death_penalty_class = TimerDeathPenalty


@pytest.fixture
def connection() -> Iterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    client.close()


@pytest.fixture
def queue(connection: Redis) -> Queue:
    return Queue(f"item22-{uuid.uuid4().hex[:8]}", connection=connection)


def _work_until_empty(queue: Queue, connection: Redis) -> None:
    """The job's own asyncio.run() opens a new event loop, so the shared
    engine's pool must hold nothing from the test session's loops, or from
    that job's afterwards."""
    asyncio.run(engine.dispose())
    worker = _InProcessWorker([queue], connection=connection)
    worker.work(burst=True, with_scheduler=False)
    asyncio.run(engine.dispose())


def _promote_due_jobs(queue: Queue, connection: Redis) -> None:
    """One pass of RQ's real scheduler, the one a worker runs."""
    scheduler = RQScheduler([queue.name], connection=connection)
    scheduler.acquire_locks()
    scheduler.enqueue_scheduled_jobs()
    scheduler.release_locks()


def _job_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == _LOGGER and r.levelno >= logging.WARNING
    ]


def test_a_retry_for_a_missing_run_returns_quietly_and_leaves_no_failure_record(
    queue: Queue, connection: Redis, caplog: pytest.LogCaptureFixture
):
    """Orphan rule 1, through a real worker loop: one WARNING naming the run,
    the job ends OK, and the failed registry stays empty. It used to raise,
    and that failure record lasted 365 days (observed)."""
    run_id = str(uuid.uuid4())
    job = queue.enqueue(_RETRY_JOB, run_id, 1, job_id=f"retry-{run_id}", job_timeout=1800)

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _work_until_empty(queue, connection)

    warnings = _job_warnings(caplog)
    assert len(warnings) == 1, warnings
    assert run_id in warnings[0] and "no longer exists" in warnings[0]
    assert queue.failed_job_registry.count == 0
    assert job.id in queue.finished_job_registry


def test_an_expired_retry_never_runs(
    queue: Queue, connection: Redis, caplog: pytest.LogCaptureFixture
):
    """The plan's proof. Two retries come due while no scheduler runs. The
    one past its TTL has lost its hash, so the scheduler removes its id and
    no worker ever sees it: no WARNING from the job, nothing failed, nothing
    finished. The other, under its TTL, runs, which shows the same scheduler
    and worker would have run the first."""
    expired = queue.enqueue_in(
        timedelta(seconds=1), _RETRY_JOB, str(uuid.uuid4()), 1, job_timeout=1800, ttl=1
    )
    alive_run_id = str(uuid.uuid4())
    alive = queue.enqueue_in(
        timedelta(seconds=1), _RETRY_JOB, alive_run_id, 1, job_timeout=1800, ttl=3600
    )

    time.sleep(2.5)
    assert not connection.exists(expired.key), "the hash should have expired"
    assert expired.id in queue.scheduled_job_registry, "RQ removes the id only when it promotes"

    _promote_due_jobs(queue, connection)
    assert expired.id not in queue.scheduled_job_registry
    assert queue.get_job_ids() == [alive.id]

    with caplog.at_level(logging.INFO, logger=_LOGGER):
        _work_until_empty(queue, connection)

    assert expired.id not in queue.finished_job_registry
    assert expired.id not in queue.failed_job_registry
    assert alive.id in queue.finished_job_registry
    warnings = _job_warnings(caplog)
    assert len(warnings) == 1 and alive_run_id in warnings[0], warnings


async def _running_run_with_failed_task() -> uuid.UUID:
    """A run whose start job is failing, as _handle_graph_failure sees it."""
    async with get_session() as session:
        org = Organization(name="Orphaned Retries Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Orphaned Retries Tender")
        session.add(tender)
        await session.commit()
        tender_id = tender.id
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        task = await uow.workflow.create_task(
            run.id, "commercial_analysis", "commercial_analysis", "commercial_agent", 0, {}
        )
        await uow.workflow.mark_task_failed(task.id, "RateLimitedError")
        run.status = "running"
        run.current_job_id = start_job_id(run.id)
        await uow.commit()
        return run.id


async def _schedule_real_retry(run_id: uuid.UUID) -> None:
    assert await workflow_jobs._handle_graph_failure(
        run_id, RateLimitedError("429", retry_after_seconds=1.0), start_job_id(run_id)
    )


def test_the_ttl_is_seven_hours_today():
    """The number, so a change to the cap or the margin is a visible choice."""
    assert run_reaper.MAX_RUN_JOB_AGE == timedelta(hours=6)
    assert workflow_jobs.retry_job_ttl_seconds() == 7 * 3600


@pytest.mark.asyncio
@pytest.mark.parametrize("cap_hours", [6, 2])
async def test_a_scheduled_retry_carries_the_run_cap_plus_one_hour(
    monkeypatch: pytest.MonkeyPatch, connection: Redis, cap_hours: int
):
    """Through the real failure handler, on RQ's real job and its Redis hash.
    The cap is changed too, to show the TTL follows it: a bare number would
    let them drift until the TTL could drop a retry a live run waits on."""
    monkeypatch.setattr(run_reaper, "MAX_RUN_JOB_AGE", timedelta(hours=cap_hours))
    expected = (cap_hours + 1) * 3600
    run_id = await _running_run_with_failed_task()

    await _schedule_real_retry(run_id)

    queue = Queue("workflow", connection=connection)
    job = queue.fetch_job(retry_job_id(run_id, 1))
    assert job is not None
    assert job.ttl == expected
    assert expected - 60 < connection.ttl(job.key) <= expected
    assert job.id in queue.scheduled_job_registry


@pytest.mark.asyncio
async def test_a_real_retry_past_its_ttl_is_dropped_by_the_scheduler(
    monkeypatch: pytest.MonkeyPatch, connection: Redis
):
    """The real failure handler's own enqueue_in, with only the length of the
    TTL shortened: its hash expires, and the scheduler removes the retry
    without queueing it. This is the workflow queue itself, where no worker
    runs in the tests, so what else the pass promotes stays queued."""
    monkeypatch.setattr(workflow_jobs, "retry_job_ttl_seconds", lambda: 1)
    run_id = await _running_run_with_failed_task()
    await _schedule_real_retry(run_id)

    queue = Queue("workflow", connection=connection)
    retry_id = retry_job_id(run_id, 1)
    assert retry_id in queue.scheduled_job_registry
    await asyncio.sleep(2.5)
    assert not connection.exists(f"rq:job:{retry_id}"), "the hash should have expired"

    _promote_due_jobs(queue, connection)

    assert retry_id not in queue.scheduled_job_registry
    assert connection.lpos(queue.key, retry_id) is None


@pytest.mark.asyncio
async def test_a_retry_the_run_still_waits_on_still_runs(monkeypatch: pytest.MonkeyPatch):
    """The rule must not over-reach: a retry that is its running run's current
    job is no orphan, whatever its age, and goes on to resume the graph."""
    resumed: list[tuple] = []

    class _Graph:
        async def ainvoke(self, *args, **kwargs):
            resumed.append(args)

    @asynccontextmanager
    async def _checkpointer() -> AsyncIterator[None]:
        yield None

    monkeypatch.setattr(workflow_jobs, "get_checkpointer", _checkpointer)
    monkeypatch.setattr(workflow_jobs, "build_graph", lambda checkpointer: _Graph())
    run_id = await _running_run_with_failed_task()
    await _schedule_real_retry(run_id)

    await workflow_jobs.retry_workflow_run(str(run_id), 1)

    assert len(resumed) == 1
