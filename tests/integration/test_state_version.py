import uuid

import pytest
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service, tender_service

pytestmark = pytest.mark.asyncio


async def test_bump_state_version_increments_by_one():
    """No B2 caller triggers this yet (Part C1's graph-relevant-mutation list -
    knowledge selection, evidence, compliance, role assignment, proposal claim,
    amendment impact - is all later-block territory), so this is its only
    coverage: a direct call against the repository method."""
    registered = await auth_service.register(
        email="statever@example.com", password="hunter2", organization_name="StateVer Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP for state version test",
        created_by=registered.user.id,
    )
    assert tender.state_version == 0

    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(tender.id)
        await uow.commit()

    updated = await tender_service.get_tender(tender.id)
    assert updated is not None
    assert updated.state_version == 1


async def test_bump_state_version_raises_for_unknown_tender():
    async with UnitOfWork() as uow:
        with pytest.raises(ValueError):
            await uow.tenders.bump_state_version(uuid.uuid4())
