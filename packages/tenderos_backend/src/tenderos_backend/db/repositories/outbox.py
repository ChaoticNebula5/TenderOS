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
