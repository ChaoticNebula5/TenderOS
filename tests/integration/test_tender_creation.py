import pytest
from sqlalchemy import select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_TENDER_CREATED
from tenderos_backend.domain.services import auth_service, tender_service

pytestmark = pytest.mark.asyncio


async def _make_org():
    return await auth_service.register(
        email="org-owner@example.com", password="hunter2", organization_name="Tender Test Org"
    )


async def test_create_tender_writes_exactly_one_outbox_event_in_same_transaction():
    registered = await _make_org()

    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP-2026-001",
        created_by=registered.user.id,
    )

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == tender.id)
        )
        events = result.scalars().all()

    assert len(events) == 1
    assert events[0].event_type == EVENT_TENDER_CREATED
    assert events[0].aggregate_type == "tender"
    assert events[0].payload["tender_id"] == str(tender.id)


async def test_create_tender_does_not_bump_state_version():
    registered = await _make_org()
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP-2026-002",
        created_by=registered.user.id,
    )
    assert tender.state_version == 0


async def test_create_tender_persists_regardless_of_redis_reachability():
    """Tender + outbox row commit to Postgres in one transaction with no Redis
    involvement at all - this is what makes the outbox pattern survive a Redis
    outage (Build Guide B2's test that an outbox event outlives Redis being
    down). Nothing
    here talks to Redis; the dispatcher (a separate process) is what would need
    Redis, and its retry-on-failure behavior is covered separately."""
    registered = await _make_org()
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP-2026-003",
        created_by=registered.user.id,
    )

    async with UnitOfWork() as uow:
        stored_tender = await uow.tenders.get(tender.id)
        events = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == tender.id)
        )

    assert stored_tender is not None
    assert len(events.scalars().all()) == 1


async def test_list_tenders_returns_only_that_organizations_tenders():
    org_a = await _make_org()
    org_b = await auth_service.register(
        email="other-org@example.com", password="hunter2", organization_name="Other Org"
    )

    await tender_service.create_tender(
        organization_id=org_a.organization.id, name="Org A Tender", created_by=org_a.user.id
    )
    await tender_service.create_tender(
        organization_id=org_b.organization.id, name="Org B Tender", created_by=org_b.user.id
    )

    org_a_tenders = await tender_service.list_tenders(org_a.organization.id)
    assert [t.name for t in org_a_tenders] == ["Org A Tender"]
