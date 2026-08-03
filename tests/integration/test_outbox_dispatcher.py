import pytest
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service, tender_service
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, _dispatch_once

pytestmark = pytest.mark.asyncio


async def test_dispatcher_marks_event_dispatched_on_success():
    """Depends on outbox_dispatcher's retry/backoff TODO being implemented -
    currently raises NotImplementedError on every pending event."""
    registered = await auth_service.register(
        email="dispatch@example.com", password="hunter2", organization_name="Dispatch Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="Dispatch Tender",
        created_by=registered.user.id,
    )

    await _dispatch_once()

    async with UnitOfWork() as uow:
        still_pending = await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)

    assert all(e.aggregate_id != tender.id for e in still_pending)
