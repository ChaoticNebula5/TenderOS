"""jobs/workflow.py::_handle_graph_failure - B16 Group 1/4's real retry
mechanism (major finding #17). Real Postgres, real Redis, no LLM cost
(classify_task_failure is a pure function; nothing here calls a model) -
proves the classify-then-schedule-via-RQ wiring end to end: a retryable
failure actually lands a real scheduled job in Redis, a terminal one
doesn't, and the run-level budget ceiling is respected.
"""

import uuid

import pytest
from redis import Redis
from rq import Queue
from tenderos_backend.agents.llm.client import ModelNotFoundError, RateLimitedError
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import retry_job_id, start_job_id
from tenderos_worker import outbox_dispatcher
from tenderos_worker.jobs.workflow import _RETRY_BUDGET_CEILING, _handle_graph_failure
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio


async def _make_org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_run_with_failed_task(
    tender_id: uuid.UUID,
    *,
    attempt_count: int = 1,
    max_attempts: int = 2,
    retry_count: int = 0,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        task = await uow.workflow.create_task(
            run.id, "commercial_analysis", "commercial_analysis", "commercial_agent", 0, {}
        )
        for _ in range(attempt_count):
            await uow.workflow.mark_task_failed(task.id, "RateLimitedError")
        task.max_attempts = max_attempts
        run.retry_count = retry_count
        # Plan item 19: what a failing start job sees, the run claimed and
        # its own job current.
        run.status = "running"
        run.current_job_id = start_job_id(run.id)
        await uow.commit()
        return run.id, task.id


async def _fail(run_id: uuid.UUID, exc: Exception) -> bool:
    """The failure handler, as the run's start job calls it."""
    return await _handle_graph_failure(run_id, exc, start_job_id(run_id))


def _scheduled_retry_job_ids(run_id: uuid.UUID) -> list[str]:
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("workflow", connection=connection)
    job_ids = queue.scheduled_job_registry.get_job_ids()
    matching = []
    for job_id in job_ids:
        job = queue.fetch_job(job_id)
        if (
            job is not None
            and job.func_name.endswith("retry_workflow_run_job")
            and job.args[0] == str(run_id)
        ):
            matching.append(job_id)
    return matching


async def test_a_retryable_failure_schedules_a_real_rq_job_and_bumps_retry_count():
    org_id, tender_id = await _make_org_and_tender("RetryScheduled")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    retried = await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is True
    assert len(_scheduled_retry_job_ids(run_id)) == 1
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    assert run.retry_count == 1


async def test_the_scheduled_retry_gets_the_workflow_jobs_timeout_not_rqs_default():
    """Phase 4 plan item 44: the retry resumes the graph like any workflow
    job, so it gets the same 1800 s the dispatcher gives the others. It was
    enqueued with no job_timeout, so RQ gave it its 180 s default."""
    org_id, tender_id = await _make_org_and_tender("RetryTimeout")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    assert await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    [job_id] = _scheduled_retry_job_ids(run_id)
    connection = Redis.from_url(settings.redis_url)
    try:
        job = Queue("workflow", connection=connection).fetch_job(job_id)
    finally:
        connection.close()
    assert job.timeout == 1800
    assert job.timeout == outbox_dispatcher._QUEUE_TIMEOUTS_SECONDS["workflow"]


async def test_a_terminal_failure_schedules_nothing_and_leaves_retry_count_unchanged():
    org_id, tender_id = await _make_org_and_tender("RetryTerminal")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    retried = await _fail(run_id, ModelNotFoundError("no such model"))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    assert run.retry_count == 0


async def test_an_exhausted_run_level_budget_overrides_an_otherwise_retryable_exception():
    org_id, tender_id = await _make_org_and_tender("RetryBudgetExhausted")
    # attempt_count/max_attempts both left with room (1/2) - only the
    # run-level ceiling should block this, proving the two ceilings are
    # checked independently, not just the per-task one.
    run_id, _task_id = await _make_run_with_failed_task(
        tender_id, retry_count=_RETRY_BUDGET_CEILING
    )

    retried = await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []


async def test_an_exhausted_per_task_ceiling_overrides_an_otherwise_retryable_exception():
    org_id, tender_id = await _make_org_and_tender("RetryTaskCeiling")
    run_id, _task_id = await _make_run_with_failed_task(
        tender_id, attempt_count=2, max_attempts=2
    )

    retried = await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []


async def test_no_failed_task_on_record_means_no_retry_not_a_crash():
    """A run with no failed WorkflowTask at all - nothing to classify
    against. Must return False cleanly, not raise."""
    org_id, tender_id = await _make_org_and_tender("RetryNoFailedTask")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()
        run_id = run.id

    retried = await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []


# --- plan item 19: the retry is the run's recorded current job ---------------


async def _current_job(run_id: uuid.UUID) -> tuple[str, str | None, int]:
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    assert run is not None
    return run.status, run.current_job_id, run.retry_count


async def test_the_retry_is_recorded_as_the_current_job_before_it_is_enqueued(monkeypatch):
    """The retry's id (numbered by the reserved retry_count) is committed as
    the run's current job before enqueue_in is called, and the job is
    enqueued under that id. Shown by an enqueue that fails, as with Redis
    down: the record is already committed, so the run names a job RQ never
    had, which the run reaper fails a minute later."""
    _org_id, tender_id = await _make_org_and_tender("RetryRecordedFirst")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)
    enqueued_as: list[str] = []

    def enqueue_fails(self, delay, fn, *args, **kwargs):
        enqueued_as.append(kwargs["job_id"])
        raise ConnectionError("redis is down")

    monkeypatch.setattr(Queue, "enqueue_in", enqueue_fails)

    with pytest.raises(ConnectionError):
        await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert enqueued_as == [retry_job_id(run_id, 1)]
    assert await _current_job(run_id) == ("running", retry_job_id(run_id, 1), 1)


async def test_a_real_retry_is_scheduled_under_its_recorded_id():
    _org_id, tender_id = await _make_org_and_tender("RetryScheduledUnderId")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    assert await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert _scheduled_retry_job_ids(run_id) == [retry_job_id(run_id, 1)]
    assert await _current_job(run_id) == ("running", retry_job_id(run_id, 1), 1)


@pytest.mark.parametrize("meanwhile", ["reaped", "another_job"])
async def test_no_retry_once_the_run_is_not_running_under_the_failing_job(meanwhile):
    """The run was reaped (failed) while the job was failing, or it is
    running under a different job: no retry is scheduled, and the budget
    reservation is undone with it."""
    _org_id, tender_id = await _make_org_and_tender(f"RetryNotCurrent-{meanwhile}")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        assert run is not None
        if meanwhile == "reaped":
            run.status = "failed"
            run.current_job_id = None
        else:
            run.current_job_id = retry_job_id(run_id, 7)
        await uow.commit()

    retried = await _fail(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []
    status, current, retry_count = await _current_job(run_id)
    assert retry_count == 0
    assert (status, current) == (
        ("failed", None) if meanwhile == "reaped" else ("running", retry_job_id(run_id, 7))
    )
