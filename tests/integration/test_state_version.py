import asyncio
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


async def test_concurrent_bumps_all_land_fixed_with_for_update():
    """B16: was `test_concurrent_bumps_lose_an_update_reproduced_not_fixed`,
    a deliberate reproduction (asserting the BUG - fewer than N bumps
    landing) with the fix left open on purpose per that pass's own explicit
    "reproduce only" instruction. `bump_state_version` is now `SELECT ...
    FOR UPDATE` + `+= 1` + flush (db/repositories/tenders.py) - a second
    concurrent caller's own `SELECT ... FOR UPDATE` for the same row blocks
    until the first transaction commits, instead of both reading the same
    stale value and racing to overwrite each other. This test is the exact
    same reproduction, assertion inverted: 10 genuinely concurrent,
    real, committed bump attempts (asyncio.gather against separate
    UnitOfWork transactions - the technique that proved the race real in
    the first place) must now all land, not just usually land.

    2 concurrent bumps alone did not reliably reproduce the ORIGINAL race
    (confirmed by running it, not assumed) - kept at 10 here too, so this
    remains a real stress test of the fix under the same conditions that
    exposed the bug, not a weaker one that would pass for the wrong reason.
    """
    registered = await auth_service.register(
        email="statever-race@example.com", password="hunter2", organization_name="StateVer Race Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP for state version race test",
        created_by=registered.user.id,
    )
    assert tender.state_version == 0

    concurrent_bumps = 10

    async def _bump() -> None:
        async with UnitOfWork() as uow:
            await uow.tenders.bump_state_version(tender.id)
            await uow.commit()

    await asyncio.gather(*(_bump() for _ in range(concurrent_bumps)))

    updated = await tender_service.get_tender(tender.id)
    assert updated is not None
    assert updated.state_version == concurrent_bumps, (
        f"expected all {concurrent_bumps} concurrent, real, committed bumps to land "
        f"(SELECT ... FOR UPDATE should serialize them, not lose any) - got "
        f"state_version={updated.state_version}, meaning at least one was lost. "
        f"The fix in db/repositories/tenders.py may have regressed - re-check "
        f"with_for_update=True is still there before assuming this is a flake."
    )
