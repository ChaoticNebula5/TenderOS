import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.audit import AuditEvent


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        organization_id: uuid.UUID,
        actor_type: str,
        actor_id: str,
        action: str,
        tender_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: uuid.UUID | None = None,
        before: dict | None = None,
        after: dict | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            organization_id=organization_id,
            tender_id=tender_id,
            workflow_run_id=workflow_run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=before,
            after=after,
            event_metadata=metadata or {},
        )
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_for_workflow_run(self, workflow_run_id: uuid.UUID) -> list[AuditEvent]:
        result = await self._session.execute(
            select(AuditEvent)
            .where(AuditEvent.workflow_run_id == workflow_run_id)
            .order_by(AuditEvent.created_at)
        )
        return list(result.scalars().all())
