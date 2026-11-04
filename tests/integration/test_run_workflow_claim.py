"""Phase 4 blocker 3: run_workflow claims its run before invoking the graph.

The outbox delivers WorkflowRunCreated at least once, so run_workflow can be
called twice for one run, one after the other or at the same moment. Only
the call that moves the run from 'queued' to 'running'
(WorkflowRepository.claim_queued_run) may invoke the graph. These tests
replace the compiled graph with a stand-in that only counts invocations:
the question is how many times the graph would start, not what it does.
"""

import asyncio
from contextlib import asynccontextmanager

import pytest
from _helpers import make_ready_run_inputs
from rq import Queue
from sqlalchemy import select
from tenderos_backend.agents.llm.client import RateLimitedError
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.workflow import WorkflowRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_WORKFLOW_RUN_STATUS_CHANGED
from tenderos_backend.domain.services import workflow_service
from tenderos_worker.jobs import workflow as workflow_jobs
from tenderos_worker.jobs.workflow import run_workflow

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10


class _CountingGraph:
    def __init__(self) -> None:
        self.invocations: list[dict] = []

    async def ainvoke(self, state, config):
        self.invocations.append(state)
        return state


def _replace_the_graph(monkeypatch: pytest.MonkeyPatch) -> _CountingGraph:
    graph = _CountingGraph()

    @asynccontextmanager
    async def _no_checkpointer():
        yield None

    monkeypatch.setattr(workflow_jobs, "get_checkpointer", _no_checkpointer)
    monkeypatch.setattr(workflow_jobs, "build_graph", lambda checkpointer: graph)
    return graph


async def _make_run(email: str):
    registered, tender = await make_ready_run_inputs(email, f"{email} org", f"{email} tender")
    return await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )


async def _status_change_events(run_id) -> list[OutboxEvent]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == run_id,
                OutboxEvent.event_type == EVENT_WORKFLOW_RUN_STATUS_CHANGED,
            )
        )
        return list(result.scalars().all())


async def test_a_duplicate_delivery_does_not_start_the_graph_twice(
    monkeypatch: pytest.MonkeyPatch,
):
    graph = _replace_the_graph(monkeypatch)
    run = await _make_run("claim-dup@example.com")

    await run_workflow(str(run.id))
    await run_workflow(str(run.id))

    assert len(graph.invocations) == 1
    async with UnitOfWork() as uow:
        after = await uow.workflow.get_run(run.id)
    assert after is not None
    assert after.status == "running"
    assert after.started_at is not None
    # Exactly one queued -> running announcement for the progress stream.
    running_events = [
        e for e in await _status_change_events(run.id) if e.payload["status"] == "running"
    ]
    assert len(running_events) == 1


async def test_two_concurrent_deliveries_start_the_graph_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
):
    """A barrier holds both deliveries until each is about to run its claim,
    so the two conditional UPDATEs really are concurrent. Postgres makes the
    second wait on the row lock and then re-checks status = 'queued' against
    the row the first wrote, so only one claim matches."""
    graph = _replace_the_graph(monkeypatch)
    run = await _make_run("claim-race@example.com")

    barrier = asyncio.Barrier(2)
    original_claim = WorkflowRepository.claim_queued_run
    claims: list[bool] = []

    async def claim_together(self, run_id, job_id):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        claimed = await original_claim(self, run_id, job_id)
        claims.append(claimed)
        return claimed

    monkeypatch.setattr(WorkflowRepository, "claim_queued_run", claim_together)

    await asyncio.wait_for(
        asyncio.gather(run_workflow(str(run.id)), run_workflow(str(run.id))),
        timeout=_WAIT_SECONDS,
    )

    assert sorted(claims) == [False, True]
    assert len(graph.invocations) == 1


class _FailsOnceThenRecords:
    """First invocation fails the way a node does: it marks its WorkflowTask
    failed, then raises a retryable error. Later invocations are recorded."""

    def __init__(self, task_id) -> None:
        self.task_id = task_id
        self.invocations: list[object] = []

    async def ainvoke(self, state, config):
        self.invocations.append(state)
        if len(self.invocations) == 1:
            async with UnitOfWork() as uow:
                await uow.workflow.mark_task_failed(self.task_id, "RateLimitedError")
                await uow.commit()
            raise RateLimitedError("429 from the provider", retry_after_seconds=1.0)
        return None


async def test_a_failed_first_attempt_is_retried_and_invokes_the_graph_again(monkeypatch):
    """The in-job retry (_handle_graph_failure) must not be blocked by the
    queued -> running claim. It is not on the claim's path: the retry is a
    separate job, retry_workflow_run_job -> retry_workflow_run, which
    resumes the checkpoint with ainvoke(None, ...) and never calls
    claim_run_for_start. The run is 'running' when the retry is scheduled
    (claimed by the first attempt; _handle_graph_failure does not change
    it, and run_workflow only syncs 'failed' when no retry was scheduled).
    A redelivered WorkflowRunCreated after that is still a no-op."""
    run = await _make_run("claim-retry@example.com")
    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            run.id, "commercial_analysis", "commercial_analysis", "commercial_agent", 0, {}
        )
        task.max_attempts = 2
        await uow.commit()
        task_id = task.id
    graph = _FailsOnceThenRecords(task_id)

    @asynccontextmanager
    async def _no_checkpointer():
        yield None

    monkeypatch.setattr(workflow_jobs, "get_checkpointer", _no_checkpointer)
    monkeypatch.setattr(workflow_jobs, "build_graph", lambda checkpointer: graph)
    scheduled: list[tuple] = []
    monkeypatch.setattr(
        Queue, "enqueue_in", lambda self, delay, fn, *args, **kwargs: scheduled.append((fn, args))
    )

    with pytest.raises(RateLimitedError):
        await run_workflow(str(run.id))

    assert len(scheduled) == 1, "the retryable failure must schedule a retry"
    retry_fn, retry_args = scheduled[0]
    assert retry_fn is workflow_jobs.retry_workflow_run_job
    async with UnitOfWork() as uow:
        at_retry = await uow.workflow.get_run(run.id)
    assert at_retry is not None and at_retry.status == "running"

    # What RQ's scheduler would run once the delay passes (the async body
    # of retry_workflow_run_job).
    await workflow_jobs.retry_workflow_run(*retry_args)
    assert len(graph.invocations) == 2
    assert graph.invocations[1] is None, "the retry resumes the checkpoint with no new input"

    # A late duplicate of the original delivery still does nothing.
    await run_workflow(str(run.id))
    assert len(graph.invocations) == 2
