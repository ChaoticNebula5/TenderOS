"""Phase 4: the worker side of async operations.

An HTTP route records an operation (operation_service.request_operation)
and returns 202; the outbox delivers OperationRequested here. run_operation
claims the operation (queued -> running, one conditional UPDATE, so a
duplicate or concurrent delivery does nothing), runs the work for its kind,
and finishes it as succeeded, or failed with a safe error code and message.
The real exception is logged here with its traceback; only the code and a
generic message reach the web app.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from rq.timeouts import JobTimeoutException
from tenderos_backend.agents.llm.client import (
    DailyQuotaExhaustedError,
    LLMClient,
    LLMTimeoutError,
    ProviderRequestRejectedError,
    RateLimitedError,
)
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.core.log_context import bind_log_context
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.session import engine
from tenderos_backend.domain.services import (
    commercial_service,
    graph_service,
    operation_service,
    proposal_service,
    redteam_service,
)

logger = logging.getLogger(__name__)

# Safe, user-facing failure details (async_operations.error_code/message).
_FAILURES: dict[str, str] = {
    "LLM_TIMEOUT": "The AI provider took too long to answer. Please try again.",
    "LLM_RATE_LIMITED": "The AI provider is busy right now. Please try again in a few minutes.",
    # PR 34: the provider's daily quota is used up; it resets with the
    # provider's day (midnight Pacific for Gemini), not in minutes.
    "LLM_DAILY_LIMIT": "The AI provider's daily limit has been reached. Please try again tomorrow.",
    # PR 29 (Q6 a): the provider refused the request itself (a 4xx other
    # than 404 and 429). Retrying the same request will not help.
    "LLM_REJECTED": "The AI provider refused this request. Please contact support if it persists.",
    "JOB_TIMEOUT": "This took longer than allowed and was stopped. Please try again.",
    "INTERNAL_ERROR": "Something went wrong on our side. Please try again.",
}


def _build_llm_client(organization_id: uuid.UUID) -> LLMClient:
    # Overridable by tests, like the other worker jobs' helpers.
    return build_llm_client(organization_id=organization_id)


async def _run_redteam(operation: AsyncOperation) -> None:
    await _project_if_stale(operation.tender_id)
    # operation_id: the pass is recorded as this operation's (plan items 94
    # and 96), so readiness can tell which pass it is reading.
    await redteam_service.run_red_team(
        _build_llm_client(operation.organization_id),
        settings.reasoning_model,
        operation.tender_id,
        operation_id=operation.id,
    )


async def _project_if_stale(tender_id: uuid.UUID) -> None:
    """A red-team pass the user asked for looks at the current graph (plan
    item 95, user decision Q4). Without this, any graph-relevant edit since
    the last projection made run_red_team skip its four graph checks (and,
    since plan item 86, leave their open findings alone), and item 95 adds
    bumps to more edits. The run's own red_team node needs nothing: it
    follows project_graph directly.

    A projection that fails is logged and the pass goes on: run_red_team
    then finds the graph stale and skips the graph checks, as before. A job
    timeout is not swallowed (run_operation handles it).
    """
    try:
        status = await graph_service.get_graph_status(tender_id)
        if not status["fresh"]:
            await graph_service.project_tender(tender_id)
    except JobTimeoutException:
        raise
    except Exception:
        logger.exception(
            "red team: could not project the stale graph for tender %s first; "
            "its graph checks will be skipped",
            tender_id,
        )


async def _run_proposal_generation(operation: AsyncOperation) -> None:
    # Stage 2: what POST .../proposal/generate used to run inside the
    # request. The drafted sections are the result; the web app re-reads
    # GET .../proposal/sections once the operation has succeeded.
    # operation_id: its calls are recorded as this operation's (PR 29).
    await proposal_service.draft_proposal_for_tender(
        _build_llm_client(operation.organization_id),
        settings.reasoning_model,
        operation.tender_id,
        operation_id=operation.id,
    )


async def _run_commercial_validation(operation: AsyncOperation) -> None:
    # Stage 2: what POST /pricing-documents/{id}/validate used to run inside
    # the request. Tender-wide, as before (cross-document checks need every
    # sheet); findings already stored are looked up, not explained again.
    # operation_id: its calls are recorded as this operation's (PR 29).
    await commercial_service.evaluate_commercial_for_tender(
        _build_llm_client(operation.organization_id),
        settings.reasoning_model,
        operation.tender_id,
        operation_id=operation.id,
    )


# kind -> the work. Every kind in async_operation.OPERATION_KINDS has one.
_HANDLERS: dict[str, Callable[[AsyncOperation], Awaitable[None]]] = {
    "redteam_run": _run_redteam,
    "proposal_generation": _run_proposal_generation,
    "commercial_validation": _run_commercial_validation,
}


def _error_code_for(exc: BaseException) -> str:
    if isinstance(exc, LLMTimeoutError):
        return "LLM_TIMEOUT"
    # Before RateLimitedError, which it subclasses.
    if isinstance(exc, DailyQuotaExhaustedError):
        return "LLM_DAILY_LIMIT"
    if isinstance(exc, RateLimitedError):
        return "LLM_RATE_LIMITED"
    if isinstance(exc, ProviderRequestRejectedError):
        return "LLM_REJECTED"
    return "INTERNAL_ERROR"


async def _fail(operation: AsyncOperation, code: str) -> bool:
    """Whether this call failed the operation (False: the claim was lost)."""
    assert operation.claim_token is not None
    finished = await operation_service.finish_operation(
        operation.id, operation.claim_token, "failed", code, _FAILURES[code]
    )
    if not finished:
        logger.warning(
            "operation %s: claim lost before it could be marked failed (%s)", operation.id, code
        )
    return finished


async def run_operation(operation_id: str) -> None:
    op_id = uuid.UUID(operation_id)
    current = await operation_service.get_operation(op_id)
    if current is None:
        logger.warning("operation %s not found; nothing to run", op_id)
        return
    # PR 30: the operation's own tender and organization, for every line
    # below (its OperationRequested payload names neither). Bound inside
    # run_operation_job's asyncio.run, so the binding ends with it.
    bind_log_context(tender_id=current.tender_id, org_id=current.organization_id)
    handler = _HANDLERS.get(current.kind)
    if handler is None:
        logger.error("operation %s has unknown kind %r; not running it", op_id, current.kind)
        return

    operation = await operation_service.claim_operation(
        op_id, operation_service.lease_for(current.kind)
    )
    if operation is None:
        logger.info(
            "operation %s not started: no longer queued (status %s); duplicate or concurrent "
            "delivery",
            op_id,
            current.status,
        )
        return
    assert operation.claim_token is not None

    try:
        await handler(operation)
    except JobTimeoutException:
        # PR 33 (user decision): RQ's timeout landing inside the work's own
        # code is a timeout, not an internal error. It goes on to
        # run_operation_job's recovery, which fails the operation as
        # JOB_TIMEOUT (and records a red-team operation's failed pass if it
        # has none), and RQ files the job as failed rather than finished.
        # JobTimeoutException is an Exception subclass, so without this the
        # clause below took it as INTERNAL_ERROR.
        raise
    except Exception as exc:
        code = _error_code_for(exc)
        logger.exception("operation %s (%s) failed: %s", op_id, operation.kind, code)
        await _fail(operation, code)
        return

    finished = await operation_service.finish_operation(op_id, operation.claim_token, "succeeded")
    if not finished:
        logger.warning(
            "operation %s: claim lost before it could be marked succeeded (the reaper may "
            "have failed it)",
            op_id,
        )


def run_operation_job(operation_id: str) -> None:
    """The RQ entry point. Like jobs/workflow.py's _run_sync, our own
    asyncio.run is on the stack, so RQ's JobTimeoutException (raised when the
    job passes its timeout) reaches code of ours: the operation is then
    marked failed as JOB_TIMEOUT instead of waiting for the reaper. The
    engine is disposed first because its pooled connections belong to the
    event loop that just stopped.

    Plan item 105 (PR 33): a red-team operation failed here also gets a
    failed pass when none was recorded. RQ's timeout landing while the event
    loop waits surfaces outside the pass, and asyncio.run cancels it, so
    run_red_team never records it; landing during the projection step, the
    pass never started. Only when this recovery failed the operation itself:
    one the reaper got to first has its pass recorded there."""
    # The first line of job code. Its timestamp, against RQ's own job
    # started_at, is the job's startup cost (forking the work-horse and
    # importing this module; see worker.py's preload).
    logger.info("operation %s: job code started", operation_id)
    try:
        asyncio.run(run_operation(operation_id))
    except JobTimeoutException:

        async def _recover() -> None:
            await engine.dispose()
            operation = await operation_service.get_operation(uuid.UUID(operation_id))
            if (
                operation is not None
                and operation.status == "running"
                and await _fail(operation, "JOB_TIMEOUT")
            ):
                await operation_service.record_failed_redteam_pass_after_timeout(operation.id)

        asyncio.run(_recover())
        raise
