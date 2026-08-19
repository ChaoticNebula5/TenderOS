"""domain/services/knowledge_service.py::set_knowledge_selection - the
state-version bump added in B10. B6's own docstring deferred this
explicitly until graph projection existed to give the freshness comparison
somewhere to compare against; B10 is that dependency, so this is the fourth
of Part C's six listed graph-relevant mutation types with a real bump call
site (compliance approve/override - B7/B8, role select/deselect - B8A,
proposal claim persist - B9, knowledge selection change - B10). Same direct
before/after assertion shape as test_compliance_service.py/
test_talent_service.py/test_proposal_service.py's own state_version tests.
"""

import uuid

import pytest
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import knowledge_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio


async def _org_tender_and_employee() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="KnowledgeSelection Org",
    )
    tender = await create_tender(
        organization_id=registered.organization.id,
        name="Knowledge Selection Tender",
        created_by=registered.user.id,
    )
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=registered.organization.id,
            name="Selection Test Employee",
            content_fingerprint=uuid.uuid4().hex,
            title="Selection Test Employee",
        )
        await uow.commit()
    return tender.id, knowledge_item.id, registered.user.id


async def test_set_knowledge_selection_bumps_tender_state_version():
    tender_id, knowledge_item_id, actor_id = await _org_tender_and_employee()

    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await knowledge_service.set_knowledge_selection(
        tender_id, knowledge_item_id, True, actor_id
    )

    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
    assert after_version == before_version + 1


async def test_set_knowledge_selection_rejects_a_knowledge_item_from_another_organization():
    """Regression test for a cross-tenant hole: tender_knowledge_selections'
    two foreign keys (tenders.id, knowledge_items.id) are independent, so
    nothing at the schema level stops org A's tender from selecting org B's
    knowledge item. Left unchecked, the graph projector would MERGE org B's
    employee node straight into org A's tender graph (projector.py's
    reachable-shared-node walk resolves knowledge_item_id with no
    organization check at all), and proposal drafting could cite org B's
    private data as evidence for an org A claim.
    """
    tender_id, _own_item_id, actor_id = await _org_tender_and_employee()
    _other_tender_id, other_org_item_id, _other_actor_id = await _org_tender_and_employee()

    with pytest.raises(knowledge_service.CrossOrganizationKnowledgeItemError):
        await knowledge_service.set_knowledge_selection(
            tender_id, other_org_item_id, True, actor_id
        )

    async with UnitOfWork() as uow:
        selections = await uow.knowledge.list_selections_for_tender(tender_id)
    assert selections == [], "the cross-org selection must not be persisted"


async def test_deselecting_also_bumps_state_version():
    # Same call site, opposite direction - selection and deselection are
    # both "knowledge selection change" per Part C's own wording, neither
    # is a no-op for freshness purposes (a deselect is exactly the mutation
    # USED_IN's selected=false property-flip exists to represent in Neo4j).
    tender_id, knowledge_item_id, actor_id = await _org_tender_and_employee()
    await knowledge_service.set_knowledge_selection(
        tender_id, knowledge_item_id, True, actor_id
    )

    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await knowledge_service.set_knowledge_selection(
        tender_id, knowledge_item_id, False, actor_id
    )

    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
    assert after_version == before_version + 1
