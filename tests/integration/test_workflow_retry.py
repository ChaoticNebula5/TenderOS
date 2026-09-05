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
        await uow.commit()
        return run.id, task.id


def _scheduled_retry_job_ids(run_id: uuid.UUID) -> list[str]:
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("workflow", connection=connection)
    job_ids = queue.scheduled_job_registry.get_job_ids()
    matching = []
    for job_id in job_ids:
        job = queue.fetch_job(job_id)
        if job is not None and job.func_name.endswith("retry_workflow_run_job") and job.args == (
            str(run_id),
        ):
            matching.append(job_id)
    return matching


async def test_a_retryable_failure_schedules_a_real_rq_job_and_bumps_retry_count():
    org_id, tender_id = await _make_org_and_tender("RetryScheduled")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    retried = await _handle_graph_failure(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is True
    assert len(_scheduled_retry_job_ids(run_id)) == 1
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    assert run.retry_count == 1


async def test_a_terminal_failure_schedules_nothing_and_leaves_retry_count_unchanged():
    org_id, tender_id = await _make_org_and_tender("RetryTerminal")
    run_id, _task_id = await _make_run_with_failed_task(tender_id)

    retried = await _handle_graph_failure(run_id, ModelNotFoundError("no such model"))

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

    retried = await _handle_graph_failure(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []


async def test_an_exhausted_per_task_ceiling_overrides_an_otherwise_retryable_exception():
    org_id, tender_id = await _make_org_and_tender("RetryTaskCeiling")
    run_id, _task_id = await _make_run_with_failed_task(
        tender_id, attempt_count=2, max_attempts=2
    )

    retried = await _handle_graph_failure(run_id, RateLimitedError("429", retry_after_seconds=1.0))

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

    retried = await _handle_graph_failure(run_id, RateLimitedError("429", retry_after_seconds=1.0))

    assert retried is False
    assert _scheduled_retry_job_ids(run_id) == []
