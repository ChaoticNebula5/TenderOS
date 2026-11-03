"""Async operations: long-running work requested over HTTP, run by the worker.

Phase 4: no LLM call runs inside an HTTP request. A route asks for an
operation (request_operation) and returns 202 with its id; the outbox
delivers OperationRequested to the worker's run_operation job, which
claims it (claim_operation), does the work and finishes it
(finish_operation); the web app follows the status through
OperationStatusChanged events on the operation's SSE stream, falling back
to polling GET /operations/{id}.

Every transition writes its outbox event in the same transaction as the
state change, so a status the web app is told about is always committed.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_ASYNC_OPERATION,
    EVENT_OPERATION_REQUESTED,
    EVENT_OPERATION_STATUS_CHANGED,
    operation_requested_idempotency_key,
    operation_requested_payload,
    operation_status_changed_idempotency_key,
    operation_status_changed_payload,
)

# Design numbers, not measured. RQ kills a job at its timeout, so each kind's
# job timeout bounds how long one run can take; the lease set at claim is
# that timeout plus a margin, after which the dispatcher's reaper fails the
# operation as WORKER_LOST. Proposal generation can make about 12 LLM calls
# of up to 180 s each (the per-call deadline, agents/llm/gemini_adapter.py);
# a red-team run makes one.
JOB_TIMEOUT_SECONDS: dict[str, int] = {
    "redteam_run": 600,
    "proposal_generation": 1800,
    "commercial_validation": 1800,
}
LEASE_MARGIN = timedelta(seconds=60)

# Phase 4 plan item 44. A queued operation is failed as DISPATCH_FAILED in
# two cases, and waiting is neither of them:
# - Lost: its OperationRequested event was dispatched (so it had an RQ job)
#   and that job will never run: RQ filed it as failed or canceled, or no
#   longer knows it, in the queue or any registry (reap_lost_queued_operations;
#   the check is the dispatcher's, which owns the Redis connection).
#   Before, any operation queued for 60 minutes was failed, even one only
#   waiting its turn behind other tenants' operations, which run one at a
#   time, up to 30 minutes each.
# - Past MAX_QUEUED_AGE, whatever RQ says (reap_queued_past_cap): the
#   last resort, for anything the lost check cannot see, such as a job RQ
#   reports finished whose operation was never claimed, or a request whose
#   dispatch never happens.
#
# Design number, not measured: 6 hours. It must not fire on a working
# system: it is 12 maximum-length operations (30 min) waiting ahead, far
# deeper than one operations worker should ever be allowed to queue (item
# 25's replicas are the fix for that), 16 times the outbox's longest retry
# span (22.5 min: 8 attempts, outbox_dispatcher.MAX_ATTEMPTS), and a
# several-hour Redis or worker outage. And it bounds what only it catches
# to 6 hours rather than forever.
# A dead-lettered request is still failed at once by the dispatcher
# (fail_undispatched_operation).
MAX_QUEUED_AGE = timedelta(hours=6)


def lease_for(kind: str) -> timedelta:
    return timedelta(seconds=JOB_TIMEOUT_SECONDS[kind]) + LEASE_MARGIN


@dataclass(frozen=True)
class OperationRequest:
    operation: AsyncOperation
    created: bool  # False: an active operation of this kind already existed


async def get_operation(operation_id: uuid.UUID) -> AsyncOperation | None:
    async with UnitOfWork() as uow:
        return await uow.operations.get(operation_id)


async def get_active_operation(tender_id: uuid.UUID, kind: str) -> AsyncOperation | None:
    """The tender's queued or running operation of this kind, if any (at
    most one, by uq_async_operation_active)."""
    async with UnitOfWork() as uow:
        return await uow.operations.get_active(tender_id, kind)


async def request_operation(
    organization_id: uuid.UUID,
    tender_id: uuid.UUID,
    kind: str,
    requested_by: uuid.UUID | None,
) -> OperationRequest:
    """Create a queued operation and its OperationRequested event, or return
    the tender's active operation of this kind if there is one (the same id
    a second click should follow, not a second run).

    The retry covers one race: the active operation finishes between the
    failed insert and the lookup, so neither finds a row. Two attempts are
    enough; a third miss would mean operations of this kind are being
    created and finished faster than this loop runs, which is a bug."""
    for _ in range(2):
        async with UnitOfWork() as uow:
            created = await uow.operations.create_queued(
                organization_id, tender_id, kind, requested_by
            )
            if created is not None:
                await uow.outbox.create(
                    aggregate_type=AGGREGATE_ASYNC_OPERATION,
                    aggregate_id=created.id,
                    event_type=EVENT_OPERATION_REQUESTED,
                    payload=operation_requested_payload(created.id, kind),
                    idempotency_key=operation_requested_idempotency_key(created.id),
                )
                await _status_event(uow, created.id, "queued")
                await uow.commit()
                return OperationRequest(operation=created, created=True)
            active = await uow.operations.get_active(tender_id, kind)
            if active is not None:
                return OperationRequest(operation=active, created=False)
    raise RuntimeError(f"could not create or find an active {kind} operation for {tender_id}")


async def claim_operation(operation_id: uuid.UUID, lease: timedelta) -> AsyncOperation | None:
    """queued -> running for the caller (see AsyncOperationRepository.claim).
    None when another delivery already claimed it, or it is no longer
    queued: the caller must not do the work."""
    async with UnitOfWork() as uow:
        claimed = await uow.operations.claim(operation_id, lease)
        if claimed is not None:
            await _status_event(uow, operation_id, "running")
            await uow.commit()
        return claimed


async def finish_operation(
    operation_id: uuid.UUID,
    claim_token: uuid.UUID,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    """running -> succeeded | failed, only while the caller holds the claim.
    False (nothing written) if the operation was reaped meanwhile."""
    async with UnitOfWork() as uow:
        finished = await uow.operations.finish(
            operation_id, claim_token, status, error_code, error_message
        )
        if finished:
            await _status_event(uow, operation_id, status)
            await uow.commit()
        return finished


async def reap_lapsed_operations(batch_size: int) -> list[uuid.UUID]:
    """Fail running operations whose lease lapsed (WORKER_LOST), at most
    batch_size, each with its status event. Run from the outbox
    dispatcher's loop."""
    async with UnitOfWork() as uow:
        reaped = await uow.operations.reap_lapsed(batch_size)
        for operation_id in reaped:
            await _status_event(uow, operation_id, "failed")
        await uow.commit()
        return reaped


async def reap_lost_queued_operations(
    batch_size: int, job_may_run: Callable[[uuid.UUID], Awaitable[bool]]
) -> list[uuid.UUID]:
    """Fail as DISPATCH_FAILED, with their status events, the queued
    operations whose request was dispatched but whose RQ job will never run
    (`job_may_run` False), at most batch_size checked per call. One still
    waiting in the queue, or running, is left alone. Run from the outbox
    dispatcher's loop, which supplies the RQ check."""
    async with UnitOfWork() as uow:
        candidates = await uow.operations.list_dispatched_queued(batch_size)
    lost = [operation_id for operation_id in candidates if not await job_may_run(operation_id)]
    if not lost:
        return []
    async with UnitOfWork() as uow:
        reaped = await uow.operations.fail_queued(lost)
        for operation_id in reaped:
            await _status_event(uow, operation_id, "failed")
        await uow.commit()
        return reaped


async def reap_queued_past_cap(batch_size: int) -> list[uuid.UUID]:
    """Fail operations still queued past MAX_QUEUED_AGE (DISPATCH_FAILED),
    at most batch_size, each with its status event, whatever RQ says, so
    the tender can request that kind of operation again. Run from the
    outbox dispatcher's loop."""
    async with UnitOfWork() as uow:
        reaped = await uow.operations.fail_stale_queued(MAX_QUEUED_AGE, batch_size)
        for operation_id in reaped:
            await _status_event(uow, operation_id, "failed")
        await uow.commit()
        return reaped


async def fail_undispatched_operation(uow: UnitOfWork, operation_id: uuid.UUID) -> bool:
    """Fail a still-queued operation as DISPATCH_FAILED, with its status
    event, inside the caller's transaction: the outbox dispatcher calls this
    in the same transaction that dead-letters the operation's
    OperationRequested event, so the two commit or roll back together. The
    caller commits. False if it was no longer queued (nothing written)."""
    failed = await uow.operations.fail_undispatched(operation_id)
    if failed:
        await _status_event(uow, operation_id, "failed")
    return failed


async def _status_event(uow: UnitOfWork, operation_id: uuid.UUID, status: str) -> None:
    await uow.outbox.create(
        aggregate_type=AGGREGATE_ASYNC_OPERATION,
        aggregate_id=operation_id,
        event_type=EVENT_OPERATION_STATUS_CHANGED,
        payload=operation_status_changed_payload(operation_id, status),
        idempotency_key=operation_status_changed_idempotency_key(operation_id, status),
    )
