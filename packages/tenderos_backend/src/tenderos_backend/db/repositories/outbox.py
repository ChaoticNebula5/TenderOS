import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.outbox import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        event_type: str,
        payload: dict,
        idempotency_key: str,
    ) -> OutboxEvent:
        event = OutboxEvent(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def get(self, event_id: uuid.UUID) -> OutboxEvent | None:
        return await self._session.get(OutboxEvent, event_id)

    async def get_pending(self, max_attempts: int, limit: int = 50) -> list[OutboxEvent]:
        result = await self._session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.status.in_(["pending", "failed"]),
                OutboxEvent.available_at <= datetime.now(UTC),
                OutboxEvent.attempt_count < max_attempts,
            )
            .order_by(OutboxEvent.available_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim_by_id(self, event_id: uuid.UUID, max_attempts: int) -> OutboxEvent | None:
        """Major finding #22's real fix: SELECT ... FOR UPDATE SKIP LOCKED
        for one SPECIFIC event, not "whatever's next" - real DB-level
        mutual exclusion between concurrent dispatcher instances. A second
        dispatcher's own identical call for the SAME id, running
        concurrently, skips it (returns None) rather than also reading and
        double-dispatching it; a call for a DIFFERENT id proceeds normally,
        genuinely concurrent, not serialized against unrelated rows.

        By id, not "claim whatever's next" (an earlier version of this
        fix did that, via ORDER BY ... LIMIT 1 FOR UPDATE SKIP LOCKED
        called in a loop) - caught by this fix's own concurrency test
        actually running, not assumed: outbox_dispatcher.py's own commit
        happens per-event, inside the same transaction that claims the
        row, so the row's own backoff `available_at` (as little as 1s for
        a first retry) can already be back in the past by the time a
        "claim whatever's next" loop cycles back around within the SAME
        _dispatch_once() call - re-claiming and re-attempting an event
        several times in one invocation instead of once, exactly the
        "sequential retry tested, genuine concurrency untested" standing
        pattern's own inverse failure mode (a fix for concurrency that
        broke a already-correct sequential/backoff guarantee). Claiming a
        FIXED candidate list (built once per _dispatch_once() call, via
        get_pending's own plain read) by id instead means each event is
        attempted at most once per invocation, matching this file's own
        pre-fix behavior exactly, while FOR UPDATE SKIP LOCKED still gives
        the real cross-dispatcher protection that behavior never had.
        """
        result = await self._session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.id == event_id,
                OutboxEvent.status.in_(["pending", "failed"]),
                OutboxEvent.available_at <= datetime.now(UTC),
                OutboxEvent.attempt_count < max_attempts,
            )
            .with_for_update(skip_locked=True)
        )
        return result.scalar_one_or_none()

    async def mark_dispatched(self, event_id: uuid.UUID) -> None:
        event = await self._session.get(OutboxEvent, event_id)
        if event is None:
            raise ValueError(f"OutboxEvent {event_id} not found")
        event.status = "dispatched"
        event.dispatched_at = datetime.now(UTC)
        await self._session.flush()

    async def mark_retry(self, event_id: uuid.UUID, available_at: datetime) -> None:
        event = await self._session.get(OutboxEvent, event_id)
        if event is None:
            raise ValueError(f"OutboxEvent {event_id} not found")
        event.status = "failed"
        event.attempt_count += 1
        event.available_at = available_at
        await self._session.flush()
