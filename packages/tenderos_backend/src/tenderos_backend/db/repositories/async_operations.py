import uuid
from datetime import timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.async_operation import (
    ACTIVE_PREDICATE_SQL,
    ACTIVE_STATUSES,
    AsyncOperation,
)

# Every time this repository writes or compares is the database's now(), as
# in IdempotencyRepository and OutboxRepository.

WORKER_LOST = "WORKER_LOST"
_WORKER_LOST_MESSAGE = "The background job stopped before finishing. Please run it again."
# The operation's request never reached a worker: its OperationRequested
# event was dead-lettered, or it stayed queued past the dispatch window.
DISPATCH_FAILED = "DISPATCH_FAILED"
_DISPATCH_FAILED_MESSAGE = "The background job could not be started. Please run it again."


class AsyncOperationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, operation_id: uuid.UUID) -> AsyncOperation | None:
        return await self._session.get(AsyncOperation, operation_id)

    async def get_active(self, tender_id: uuid.UUID, kind: str) -> AsyncOperation | None:
        result = await self._session.execute(
            select(AsyncOperation).where(
                AsyncOperation.tender_id == tender_id,
                AsyncOperation.kind == kind,
                AsyncOperation.status.in_(ACTIVE_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def create_queued(
        self,
        organization_id: uuid.UUID,
        tender_id: uuid.UUID,
        kind: str,
        requested_by: uuid.UUID | None,
    ) -> AsyncOperation | None:
        """Insert a queued operation, or nothing if this tender already has
        an active (queued or running) operation of this kind: the partial
        unique index uq_async_operation_active decides, so two concurrent
        requests cannot both create one. Returns the new row, or None."""
        result = await self._session.execute(
            pg_insert(AsyncOperation)
            .values(
                organization_id=organization_id,
                tender_id=tender_id,
                kind=kind,
                status="queued",
                requested_by=requested_by,
            )
            .on_conflict_do_nothing(
                index_elements=["tender_id", "kind"],
                index_where=text(ACTIVE_PREDICATE_SQL),
            )
            .returning(AsyncOperation)
        )
        return result.scalar_one_or_none()

    async def claim(self, operation_id: uuid.UUID, lease: timedelta) -> AsyncOperation | None:
        """Move a queued operation to running for the caller: a fresh claim
        token, started_at = now() and locked_until = now() + lease. One
        conditional UPDATE, so of two concurrent deliveries only one
        matches (the second waits on the row lock, then re-checks
        status = 'queued' against the row the first wrote). Returns the
        claimed row, or None if the operation is no longer queued."""
        result = await self._session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id == operation_id, AsyncOperation.status == "queued")
            .values(
                status="running",
                started_at=func.now(),
                locked_until=func.now() + lease,
                claim_token=uuid.uuid4(),
            )
            .returning(AsyncOperation)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()

    async def finish(
        self,
        operation_id: uuid.UUID,
        claim_token: uuid.UUID,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> bool:
        """Set a running operation's final status, only while the caller
        still holds the claim. Returns False, writing nothing, if the
        operation was reaped (failed with WORKER_LOST) or is otherwise no
        longer this caller's."""
        if status not in ("succeeded", "failed"):
            raise ValueError(f"not a final status: {status!r}")
        result = await self._session.execute(
            update(AsyncOperation)
            .where(
                AsyncOperation.id == operation_id,
                AsyncOperation.claim_token == claim_token,
                AsyncOperation.status == "running",
            )
            .values(
                status=status,
                error_code=error_code,
                error_message=error_message,
                finished_at=func.now(),
            )
            .returning(AsyncOperation.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def reap_lapsed(self, batch_size: int) -> list[uuid.UUID]:
        """Fail running operations whose lease has lapsed (the worker died
        or was killed without finishing) with error_code WORKER_LOST, at
        most batch_size per call, and return their ids. Never re-runs them:
        that would repeat paid LLM calls; the user starts it again.

        The batch is a MATERIALIZED CTE, for the reason
        IdempotencyRepository.delete_expired gives: a LIMIT ... FOR UPDATE
        subquery inside the statement's WHERE can be re-run per row and
        exceed its limit."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        lapsed = (
            select(AsyncOperation.id)
            .where(AsyncOperation.status == "running", AsyncOperation.locked_until < func.now())
            .order_by(AsyncOperation.locked_until)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("lapsed_operations")
            .prefix_with("MATERIALIZED")
        )
        result = await self._session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id.in_(select(lapsed.c.id)))
            .values(
                status="failed",
                error_code=WORKER_LOST,
                error_message=_WORKER_LOST_MESSAGE,
                finished_at=func.now(),
            )
            .returning(AsyncOperation.id)
            .execution_options(synchronize_session=False)
        )
        return list(result.scalars().all())

    async def fail_undispatched(self, operation_id: uuid.UUID) -> bool:
        """Fail a still-queued operation as DISPATCH_FAILED (its request
        event was dead-lettered). Only while it is queued: one that a worker
        already claimed is left to that worker. Returns whether it changed."""
        result = await self._session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id == operation_id, AsyncOperation.status == "queued")
            .values(
                status="failed",
                error_code=DISPATCH_FAILED,
                error_message=_DISPATCH_FAILED_MESSAGE,
                finished_at=func.now(),
            )
            .returning(AsyncOperation.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def fail_stale_queued(self, window: timedelta, batch_size: int) -> list[uuid.UUID]:
        """Fail operations still queued more than `window` after they were
        requested, as DISPATCH_FAILED, at most batch_size per call, and
        return their ids. A worker that receives one later finds it no
        longer queued and does nothing (claim). Same MATERIALIZED CTE as
        reap_lapsed; the CTE's FOR UPDATE re-checks status = 'queued' on the
        newest row version, so an operation claimed meanwhile is skipped."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        stale = (
            select(AsyncOperation.id)
            .where(
                AsyncOperation.status == "queued",
                AsyncOperation.created_at < func.now() - window,
            )
            .order_by(AsyncOperation.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("stale_queued_operations")
            .prefix_with("MATERIALIZED")
        )
        result = await self._session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id.in_(select(stale.c.id)))
            .values(
                status="failed",
                error_code=DISPATCH_FAILED,
                error_message=_DISPATCH_FAILED_MESSAGE,
                finished_at=func.now(),
            )
            .returning(AsyncOperation.id)
            .execution_options(synchronize_session=False)
        )
        return list(result.scalars().all())
