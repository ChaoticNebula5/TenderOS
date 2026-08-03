import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.idempotency import IdempotencyRecord


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
        expires_at: datetime,
    ) -> IdempotencyRecord:
        record = IdempotencyRecord(
            organization_id=organization_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            status="in_progress",
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def complete(
        self, record_id: uuid.UUID, response_status: int, response_body: dict
    ) -> None:
        record = await self._session.get(IdempotencyRecord, record_id)
        if record is None:
            raise ValueError(f"IdempotencyRecord {record_id} not found")
        record.status = "completed"
        record.response_status = response_status
        record.response_body = response_body
        await self._session.flush()

    async def fail(self, record_id: uuid.UUID) -> None:
        record = await self._session.get(IdempotencyRecord, record_id)
        if record is None:
            raise ValueError(f"IdempotencyRecord {record_id} not found")
        record.status = "failed"
        await self._session.flush()
