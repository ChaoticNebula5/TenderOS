import uuid
from datetime import timedelta

from sqlalchemy import Integer, cast, delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.idempotency import IdempotencyRecord

# Phase 4 blocker 2: every time value this repository writes or compares
# comes from the database clock (func.now()), never from the application
# host's clock. Callers pass durations (a lease, a retention period), not
# timestamps, so a clock difference between the API host and Postgres
# cannot shorten or stretch a lease. Note that Postgres now() is the start
# time of the current transaction, so each method that reads the clock is
# meant to run in its own short UnitOfWork. complete() and release() do not
# read it; complete() may run in the route's own transaction (plan item 21).


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, organization_id: uuid.UUID, operation: str, idempotency_key: str
    ) -> IdempotencyRecord | None:
        result = await self._session.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.organization_id == organization_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_in_progress(
        self,
        organization_id: uuid.UUID,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        lease: timedelta,
        retention: timedelta,
    ) -> IdempotencyRecord:
        """Insert a new in_progress record held by a fresh claim_token, with
        locked_until = now() + lease and expires_at = now() + retention,
        both on the database clock. Raises IntegrityError (uq_idempotency_key)
        if another request inserted the same key first."""
        result = await self._session.execute(
            insert(IdempotencyRecord)
            .values(
                organization_id=organization_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                status="in_progress",
                claim_token=uuid.uuid4(),
                locked_until=func.now() + lease,
                expires_at=func.now() + retention,
            )
            .returning(IdempotencyRecord)
        )
        return result.scalar_one()

    async def lease_seconds_remaining(self, record_id: uuid.UUID) -> int | None:
        """Whole seconds left on an in_progress record's lease, rounded up,
        computed in SQL. None when there is no live lease: the record is
        gone, is not in_progress, has locked_until NULL (a row from before
        migration 019) or its lease has already lapsed."""
        result = await self._session.execute(
            select(
                cast(
                    func.ceil(func.extract("epoch", IdempotencyRecord.locked_until - func.now())),
                    Integer,
                )
            ).where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.status == "in_progress",
                IdempotencyRecord.locked_until > func.now(),
            )
        )
        return result.scalar_one_or_none()

    async def take_over_expired(
        self, record_id: uuid.UUID, lease: timedelta
    ) -> IdempotencyRecord | None:
        """Give the caller the claim on an in_progress record whose lease
        has lapsed (or is NULL, from before migration 019): a fresh
        claim_token and locked_until = now() + lease. Returns the updated
        row, or None if nothing matched. Does not commit.

        The lease check must sit inside this single UPDATE. When two takers
        race, Postgres makes the second wait on the row lock, then re-checks
        its WHERE against the row the first one wrote; that row's lease is
        live, so the second matches nothing and exactly one taker wins. A
        SELECT followed by an UPDATE would let both see the lapsed lease and
        both win.
        """
        result = await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.status == "in_progress",
                or_(
                    IdempotencyRecord.locked_until.is_(None),
                    IdempotencyRecord.locked_until < func.now(),
                ),
            )
            .values(claim_token=uuid.uuid4(), locked_until=func.now() + lease)
            .returning(IdempotencyRecord)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()

    async def complete(
        self,
        record_id: uuid.UUID,
        claim_token: uuid.UUID,
        response_status: int,
        response_body: dict,
    ) -> bool:
        """Mark the record completed with the response to replay, but only
        while this caller still holds the claim (same claim_token, still
        in_progress). Returns False, writing nothing, if the claim was taken
        over or the record is gone."""
        result = await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.claim_token == claim_token,
                IdempotencyRecord.status == "in_progress",
            )
            .values(
                status="completed",
                response_status=response_status,
                response_body=response_body,
            )
            .returning(IdempotencyRecord.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def release(self, record_id: uuid.UUID, claim_token: uuid.UUID) -> bool:
        """Delete an in_progress record so its key can be used again, but
        only while this caller still holds the claim. Returns False,
        deleting nothing, if the claim was taken over, the record already
        completed, or it is gone. status = 'in_progress' is checked too, so
        a request that completed and then raised cannot delete its own
        finished record."""
        result = await self._session.execute(
            delete(IdempotencyRecord)
            .where(
                IdempotencyRecord.id == record_id,
                IdempotencyRecord.claim_token == claim_token,
                IdempotencyRecord.status == "in_progress",
            )
            .returning(IdempotencyRecord.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None

    async def delete_expired(self, batch_size: int) -> int:
        """Delete at most batch_size records whose expires_at has passed
        (database clock), oldest first, and return how many were deleted.

        One bounded batch per call, so a large backlog never becomes one
        long-running DELETE holding many row locks. The caller (blocker 3's
        dispatcher loop) calls it once per tick and commits each batch.
        SKIP LOCKED lets two callers run at once without blocking each other
        or deleting the same row twice.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        # A MATERIALIZED CTE, not `id IN (SELECT ... LIMIT n FOR UPDATE ...)`.
        # With the subquery form, one call deleted 5 rows with batch_size=2
        # (caught by test_delete_expired_removes_only_expired_rows_in_
        # bounded_batches). Checked on Postgres 16.13 with EXPLAIN ANALYZE in
        # a rolled-back transaction: the planner made the subquery the inner
        # side of a nested-loop semi join and re-ran it once per outer row
        # (LockRows loops=5). On each re-run, FOR UPDATE met the rows this
        # same DELETE had already deleted and skipped them (nodeLockRows.c,
        # REL_16_STABLE, case TM_SelfModified: "treat the tuple as deleted
        # and do not process"), so the LIMIT reached past them to the next
        # oldest rows, and every outer row found itself in a new "batch".
        # SKIP LOCKED played no part: FOR UPDATE without it also deleted 5,
        # and the same subquery with no locking clause deleted 2. It needs
        # the batch rows to come early in the outer scan, which is why it
        # depends on row order. A MATERIALIZED CTE is computed once (LockRows
        # loops=1); later rescans read its stored result.
        expired_batch = (
            select(IdempotencyRecord.id)
            .where(IdempotencyRecord.expires_at < func.now())
            .order_by(IdempotencyRecord.expires_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("expired_batch")
            .prefix_with("MATERIALIZED")
        )
        result = await self._session.execute(
            delete(IdempotencyRecord)
            .where(IdempotencyRecord.id.in_(select(expired_batch.c.id)))
            .returning(IdempotencyRecord.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())
