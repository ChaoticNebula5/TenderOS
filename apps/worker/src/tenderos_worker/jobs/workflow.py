import asyncio
import logging
import uuid
from collections.abc import Coroutine
from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from redis import Redis
from rq import Queue
from rq.timeouts import JobTimeoutException
from tenderos_backend.db.session import engine
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import (
    is_cancelled,
    mark_run_failed_on_timeout,
    sync_run_status,
)
from tenderos_backend.orchestration.checkpoint import get_checkpointer
from tenderos_backend.orchestration.graph import build_graph
from tenderos_backend.orchestration.retry import classify_task_failure
from tenderos_backend.orchestration.state import TenderState

from tenderos_worker.settings import settings

logger = logging.getLogger(__name__)

# TenderState.retry_budget_remaining's own seed value (jobs/workflow.py's
# initial_state below), reused here as a constant ceiling - see
# _handle_graph_failure's own docstring for why the LIVE countdown lives in
# WorkflowRun.retry_count instead of that LangGraph-checkpointed field.
_RETRY_BUDGET_CEILING = 10


async def _handle_graph_failure(run_id: uuid.UUID, exc: Exception) -> bool:
    """Shared by run_workflow/resume_workflow/retry_workflow_run's own
    except blocks - major finding #17's real fix (B16 Group 4/1). Returns
    True if a retry was scheduled (caller should NOT mark the run
    'failed' - it's expected to recover), False otherwise.

    Classifies the failure (orchestration/retry.py::classify_task_failure)
    against the most recently failed WorkflowTask for this run - the one
    whose own `except Exception: mark_task_failed(...); raise` put it
    there right before this exception reached here - and this run's own
    retry_count/ceiling.

    If retryable, schedules a NEW RQ job via Queue.enqueue_in - real
    backoff (classify_task_failure's own delay_seconds), survives a
    worker crash (once enqueue_in returns, the schedule lives in Redis,
    not this process's memory), and does NOT re-enter the failed node
    in-process. The new job (retry_workflow_run) resumes the SAME
    LangGraph checkpoint thread with `ainvoke(None, config)` - LangGraph's
    own idiom for "continue from the last committed checkpoint, no new
    external input" - which naturally re-executes only the super-step
    that never got checkpointed (the one that crashed), not the whole
    workflow from scratch.

    Deliberately does not itself mark the run failed or re-raise -
    callers still do both regardless of this function's return value, so
    THIS attempt stays inspectable in RQ's own failed-job registry (I4:
    "failed jobs remain inspectable") even when a retry is already
    scheduled to fix it.
    """
    async with UnitOfWork() as uow:
        tasks = await uow.workflow.list_tasks(run_id)
        failed_task = max(
            (t for t in tasks if t.status == "failed"),
            key=lambda t: t.completed_at or t.created_at,
            default=None,
        )
        run = await uow.workflow.get_run(run_id)

    if failed_task is None or run is None:
        # No WorkflowTask recorded this failure (an exception from outside
        # any node's own try/except, or the run itself vanished) - nothing
        # to classify against, and no reliable per-task ceiling to check.
        # Not retried; the caller's own sync_run_status("failed") stands.
        return False

    decision = classify_task_failure(
        exc,
        attempt_count=failed_task.attempt_count,
        max_attempts=failed_task.max_attempts,
        retry_budget_remaining=_RETRY_BUDGET_CEILING - run.retry_count,
    )
    if not decision.should_retry:
        return False

    async with UnitOfWork() as uow:
        reserved = await uow.workflow.try_reserve_retry_budget(
            run_id, cost=decision.budget_cost, ceiling=_RETRY_BUDGET_CEILING
        )
        await uow.commit()
    if not reserved:
        # classify_task_failure's own decision above was computed against
        # a plain read of run.retry_count, taken before this reservation -
        # correct advice most of the time, but not a guarantee under real
        # concurrency (two failure handlers for the same run, racing).
        # The atomic reservation above is the actual source of truth: if
        # it didn't happen, either this decision's own read was already
        # stale (a concurrent caller spent the last unit first) or the
        # run was genuinely already at the ceiling - either way, do not
        # schedule a retry the budget doesn't actually cover.
        return False

    connection = Redis.from_url(settings.redis_url)
    queue = Queue("workflow", connection=connection)
    queue.enqueue_in(timedelta(seconds=decision.delay_seconds), retry_workflow_run_job, str(run_id))
    logger.info(
        "workflow run %s: scheduled a retry in %.1fs after %s (task %s, attempt %d/%d)",
        run_id,
        decision.delay_seconds,
        type(exc).__name__,
        failed_task.task_key,
        failed_task.attempt_count,
        failed_task.max_attempts,
    )
    return True


async def run_workflow(workflow_run_id: str) -> None:
    """Enqueued on WorkflowRunCreated (see outbox_dispatcher.py's routing table).

    Builds the graph's initial TenderState from the workflow_run + tender rows
    and invokes it fresh - runs until the graph either completes or pauses at
    the first interrupt() (requirement_gate).
    """
    run_id = uuid.UUID(workflow_run_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        if run is None:
            raise ValueError(f"WorkflowRun {run_id} not found")
        tender = await uow.tenders.get(run.tender_id)
        if tender is None:
            raise ValueError(f"Tender {run.tender_id} not found")

    if run.status == "cancelled":
        # I6: both queue entry points are the only places that ever call
        # graph.ainvoke() - checking here means a cancelled run never even
        # starts, no LangGraph execution at all.
        logger.info("workflow run %s is cancelled, skipping", run_id)
        return

    initial_state: TenderState = {
        "tender_id": str(tender.id),
        "organization_id": str(tender.organization_id),
        "workflow_run_id": str(run.id),
        "langgraph_thread_id": run.langgraph_thread_id,
        "status": "created",
        "plan_version": 0,
        "active_gate_id": None,
        "revision_count": 0,
        "max_revisions": 2,
        "retry_budget_remaining": 10,
        "dispatch_task_ids": [],
        "completed_task_ids": [],
        "failed_task_ids": [],
        "unverified_requirement_ids": [],
        "open_critical_finding_ids": [],
        "changed_knowledge_item_ids": [],
        "readiness_report_id": None,
        "graph_projection_version": None,
        "degraded_stages": [],
    }
    config: RunnableConfig = {"configurable": {"thread_id": run.langgraph_thread_id}}

    logger.info("starting workflow run %s", run_id)
    try:
        async with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            await graph.ainvoke(initial_state, config)
        logger.info("workflow run %s paused or completed", run_id)
    except Exception as exc:
        # Individual specialist nodes mark their own WorkflowTask failed
        # before re-raising (see orchestration/nodes/compliance.py etc.),
        # but nothing upstream of graph.ainvoke() previously caught that
        # re-raise to move the RUN to a terminal status - an exception here
        # left workflow_runs.status stuck at whatever non-terminal value it
        # last had, forever (see KNOWN_LIMITATIONS.md's B0-B11 catch-up
        # review). ingestion.py's parse_and_embed_document is the one job
        # in this codebase that already does this correctly; mirrored here.
        logger.exception("workflow run %s failed", run_id)
        # The status==cancelled check above only covers the moment BEFORE
        # ainvoke() started - a cancellation recorded while a node is
        # mid-flight lands here too when that node's own work then raises.
        # Re-checking here, not just before, is what stops "failed" from
        # silently overwriting a real, deliberate cancellation.
        if not await is_cancelled(run_id):
            retried = await _handle_graph_failure(run_id, exc)
            if not retried:
                await sync_run_status(run_id, "failed")
        raise


async def resume_workflow(workflow_run_id: str, gate_id: str) -> None:
    """Enqueued on GateDecisionRecorded (see outbox_dispatcher.py's routing
    table). Resumes the exact paused thread with the recorded decision -
    NOT a fresh invocation (R1: "resume the exact checkpointed thread").
    """
    run_id = uuid.UUID(workflow_run_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        gate = await uow.gates.get(uuid.UUID(gate_id))
    if run is None:
        raise ValueError(f"WorkflowRun {run_id} not found")
    if gate is None or gate.decision_payload is None:
        raise ValueError(f"ApprovalGate {gate_id} has no recorded decision")

    if run.status == "cancelled":
        logger.info("workflow run %s is cancelled, skipping resume", run_id)
        return

    config: RunnableConfig = {"configurable": {"thread_id": run.langgraph_thread_id}}

    resume_command: Command[str] = Command(resume=gate.decision_payload)

    try:
        async with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)

            await graph.ainvoke(resume_command, config)
    except Exception as exc:
        # See run_workflow's identical catch above - same gap, same fix,
        # including the re-check of cancellation-during-execution.
        logger.exception("workflow run %s failed while resuming", run_id)
        if not await is_cancelled(run_id):
            retried = await _handle_graph_failure(run_id, exc)
            if not retried:
                await sync_run_status(run_id, "failed")
        raise


async def retry_workflow_run(workflow_run_id: str) -> None:
    """B16 Group 1/4's real retry mechanism - scheduled by
    _handle_graph_failure via Queue.enqueue_in, never called directly by
    an outbox route. Resumes the SAME LangGraph checkpoint thread with no
    new input (`ainvoke(None, config)`) - LangGraph's own idiom for
    "continue from the last committed checkpoint." Since a crashed
    super-step never gets checkpointed (a real Neo4j-style auto-commit-
    per-statement risk does NOT apply to LangGraph's own Postgres
    checkpointer, which commits a whole super-step atomically or not at
    all), this naturally re-executes only the super-step that failed -
    the same one `run_workflow`/`resume_workflow` were mid-way through -
    not the entire workflow from scratch. Deliberately not "re-enter the
    node": no node function is called directly here at all, only the
    compiled graph's own `ainvoke`, the same entry point every other real
    invocation in this file uses.
    """
    run_id = uuid.UUID(workflow_run_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    if run is None:
        raise ValueError(f"WorkflowRun {run_id} not found")

    if run.status == "cancelled":
        logger.info("workflow run %s is cancelled, skipping retry", run_id)
        return

    config: RunnableConfig = {"configurable": {"thread_id": run.langgraph_thread_id}}

    logger.info("retrying workflow run %s (attempt %d)", run_id, run.retry_count)
    try:
        async with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            await graph.ainvoke(None, config)
        logger.info("workflow run %s paused or completed after retry", run_id)
    except Exception as exc:
        logger.exception("workflow run %s failed again during retry", run_id)
        if not await is_cancelled(run_id):
            retried = await _handle_graph_failure(run_id, exc)
            if not retried:
                await sync_run_status(run_id, "failed")
        raise


def _run_sync(coro: Coroutine[Any, None, None], run_id: uuid.UUID) -> None:
    """The actual RQ job entrypoint for run_workflow/resume_workflow/
    retry_workflow_run - see outbox_dispatcher.py's own dispatch functions
    and _handle_graph_failure's enqueue_in call above, all three now point
    at *_job below instead of the bare coroutine functions.

    Why this exists: rq/job.py::_execute detects that a directly-enqueued
    async function returns a coroutine and calls `loop.run_until_complete`
    on OUR BEHALF - which means when RQ's own death-penalty SIGALRM fires
    on a job that's run too long, it interrupts asyncio's event loop
    itself (confirmed against a real stuck run: the traceback bottoms out
    inside asyncio's own _run_once/selectors.select, deep inside RQ's own
    job.py, not at any awaited line inside run_workflow's own try block).
    Nothing of OURS is on the call stack at that point, so run_workflow's
    own `except Exception` never runs, and the run is left at whatever
    non-terminal status it last had - forever, since nothing else
    reconciles a run stuck this way (checked: no sweeper exists).

    Providing our OWN asyncio.run() call as the thing RQ calls directly
    instead puts a real stack frame of ours in the path a
    JobTimeoutException unwinds through on its way up - it's a plain
    Exception subclass (rq.timeouts.JobTimeoutException -> BaseTimeoutException
    -> Exception), no special signal-handling needed on our side, just an
    ordinary except clause in a position that's actually reachable.

    Deliberately narrow: catches ONLY JobTimeoutException, not a bare
    `except Exception`. Every OTHER failure mode is already handled
    correctly inside run_workflow/resume_workflow/retry_workflow_run's own
    try/except (which marks the run failed or schedules a retry, then
    re-raises) - by the time any other exception reaches here, that
    handling has already run, and re-raising it unchanged is what keeps it
    in RQ's own FailedJobRegistry (I4: "failed jobs remain inspectable"),
    the same reason those functions re-raise instead of swallowing.
    """
    try:
        asyncio.run(coro)
    except JobTimeoutException:
        # A fresh asyncio.run(), not a continuation of the one above - the
        # loop that just died is gone. db/session.py's `engine` is a
        # module-level singleton whose connection pool can still be
        # holding connections bound to that now-closed loop (confirmed
        # directly - reproducing this exact sequence without disposing
        # first raised "Future attached to a different loop" from asyncpg
        # deep inside pool cleanup); SQLAlchemy's own documented remedy for
        # reusing an async engine across more than one asyncio.run() call
        # in the same process is AsyncEngine.dispose(), which resets the
        # pool so the new loop opens fresh connections instead of reusing
        # ones the old loop's shutdown left behind.
        async def _recover() -> None:
            await engine.dispose()
            await mark_run_failed_on_timeout(run_id)

        asyncio.run(_recover())
        raise


def run_workflow_job(workflow_run_id: str) -> None:
    _run_sync(run_workflow(workflow_run_id), uuid.UUID(workflow_run_id))


def resume_workflow_job(workflow_run_id: str, gate_id: str) -> None:
    _run_sync(resume_workflow(workflow_run_id, gate_id), uuid.UUID(workflow_run_id))


def retry_workflow_run_job(workflow_run_id: str) -> None:
    _run_sync(retry_workflow_run(workflow_run_id), uuid.UUID(workflow_run_id))
