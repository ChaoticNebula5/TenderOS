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
from tenderos_backend.agents.llm.client import LLMClient, LLMTimeoutError, RateLimitedError
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.session import engine
from tenderos_backend.domain.services import (
    commercial_service,
    operation_service,
    proposal_service,
    redteam_service,
)

logger = logging.getLogger(__name__)

# Safe, user-facing failure details (async_operations.error_code/message).
_FAILURES: dict[str, str] = {
    "LLM_TIMEOUT": "The AI provider took too long to answer. Please try again.",
    "LLM_RATE_LIMITED": "The AI provider is busy right now. Please try again in a few minutes.",
    "JOB_TIMEOUT": "This took longer than allowed and was stopped. Please try again.",
    "INTERNAL_ERROR": "Something went wrong on our side. Please try again.",
}


def _build_llm_client() -> LLMClient:
    # Overridable by tests, like the other worker jobs' helpers.
    return build_llm_client()


async def _run_redteam(operation: AsyncOperation) -> None:
    await redteam_service.run_red_team(
        _build_llm_client(), settings.reasoning_model, operation.tender_id
    )


async def _run_proposal_generation(operation: AsyncOperation) -> None:
    # Stage 2: what POST .../proposal/generate used to run inside the
    # request. The drafted sections are the result; the web app re-reads
    # GET .../proposal/sections once the operation has succeeded.
    await proposal_service.draft_proposal_for_tender(
        _build_llm_client(), settings.reasoning_model, operation.tender_id
    )


async def _run_commercial_validation(operation: AsyncOperation) -> None:
    # Stage 2: what POST /pricing-documents/{id}/validate used to run inside
    # the request. Tender-wide, as before (cross-document checks need every
    # sheet); findings already stored are looked up, not explained again.
    await commercial_service.evaluate_commercial_for_tender(
        _build_llm_client(), settings.reasoning_model, operation.tender_id
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
    if isinstance(exc, RateLimitedError):
        return "LLM_RATE_LIMITED"
    return "INTERNAL_ERROR"


async def _fail(operation: AsyncOperation, code: str) -> None:
    assert operation.claim_token is not None
    finished = await operation_service.finish_operation(
        operation.id, operation.claim_token, "failed", code, _FAILURES[code]
    )
    if not finished:
        logger.warning(
            "operation %s: claim lost before it could be marked failed (%s)", operation.id, code
        )


async def run_operation(operation_id: str) -> None:
    op_id = uuid.UUID(operation_id)
    current = await operation_service.get_operation(op_id)
    if current is None:
        logger.warning("operation %s not found; nothing to run", op_id)
        return
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
    event loop that just stopped."""
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
            if operation is not None and operation.status == "running":
                await _fail(operation, "JOB_TIMEOUT")

        asyncio.run(_recover())
        raise
