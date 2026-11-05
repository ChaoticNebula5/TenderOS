import uuid
from collections.abc import Awaitable, Callable
from datetime import date

from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_TENDER,
    EVENT_TENDER_CREATED,
    tender_created_idempotency_key,
    tender_created_payload,
)


async def create_tender(
    organization_id: uuid.UUID,
    name: str,
    created_by: uuid.UUID,
    client_name: str | None = None,
    submission_date: date | None = None,
    before_commit: Callable[[UnitOfWork, Tender], Awaitable[None]] | None = None,
) -> Tender:
    """Create a tender and, in the same transaction, an outbox event announcing it.

    Per Build Guide Part C1's state-version invariant list, tender creation is NOT
    a graph-relevant mutation (that list is knowledge selection / evidence /
    compliance / role assignment / proposal claim / amendment impact changes only)
    - so state_version is intentionally left untouched here.

    before_commit (plan item 21) runs last inside the transaction, with the
    tender flushed: the route completes its idempotency record there, so the
    tender and its replayable response commit together. If it raises, nothing
    is created.
    """
    async with UnitOfWork() as uow:
        tender = await uow.tenders.create(
            organization_id=organization_id,
            name=name,
            client_name=client_name,
            submission_date=submission_date,
            created_by=created_by,
        )
        await uow.outbox.create(
            aggregate_type=AGGREGATE_TENDER,
            aggregate_id=tender.id,
            event_type=EVENT_TENDER_CREATED,
            payload=tender_created_payload(tender.id, organization_id, name),
            idempotency_key=tender_created_idempotency_key(tender.id),
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(created_by),
            action="create_tender",
            tender_id=tender.id,
            target_type="tender",
            target_id=tender.id,
        )
        if before_commit is not None:
            await before_commit(uow, tender)
        await uow.commit()
        return tender


async def list_tenders(organization_id: uuid.UUID) -> list[Tender]:
    async with UnitOfWork() as uow:
        return await uow.tenders.list_for_organization(organization_id)


async def get_tender(tender_id: uuid.UUID) -> Tender | None:
    async with UnitOfWork() as uow:
        return await uow.tenders.get(tender_id)


async def update_tender(
    tender_id: uuid.UUID,
    name: str | None = None,
    client_name: str | None = None,
    submission_date: date | None = None,
    status: str | None = None,
) -> Tender | None:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.update(
            tender_id,
            name=name,
            client_name=client_name,
            submission_date=submission_date,
            status=status,
        )
        if tender is None:
            return None
        await uow.commit()
        return tender
