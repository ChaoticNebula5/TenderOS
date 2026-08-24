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
from datetime import date

import pytest
from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, knowledge_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio


async def _org_tender_and_employee() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
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
    return registered.organization.id, tender.id, knowledge_item.id, registered.user.id


async def test_set_knowledge_selection_bumps_tender_state_version():
    _org_id, tender_id, knowledge_item_id, actor_id = await _org_tender_and_employee()

    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await knowledge_service.set_knowledge_selection(tender_id, knowledge_item_id, True, actor_id)

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
    _org_id, tender_id, _own_item_id, actor_id = await _org_tender_and_employee()
    _other_org_id, _other_tender_id, other_org_item_id, _other_actor_id = (
        await _org_tender_and_employee()
    )

    with pytest.raises(knowledge_service.CrossOrganizationKnowledgeItemError):
        await knowledge_service.set_knowledge_selection(
            tender_id, other_org_item_id, True, actor_id
        )

    async with UnitOfWork() as uow:
        selections = await uow.knowledge.list_selections_for_tender(tender_id)
    assert selections == [], "the cross-org selection must not be persisted"


async def test_set_knowledge_selection_rejects_a_tender_outside_the_authorized_organization():
    """CodeRabbit follow-up to the fix above: the original guard only
    compared knowledge_item.organization_id to tender.organization_id -
    the two RESOURCES to each other - never to the org the CALLER was
    actually authorized for. A caller authorized for org A who happens to
    know a tender_id and knowledge_item_id that both belong to org B would
    pass that guard (the two resources agree with each other) and write a
    selection - and bump org B's own state_version - despite never having
    been authorized for org B at all. Fixed not by adding an organization_id
    parameter (a prior draft of this fix did that, then removed it - see
    set_knowledge_selection's own docstring: every real caller only ever
    had one way to produce that argument, re-deriving it from tender_id, so
    the comparison was always true and caught nothing) but by deriving the
    actor's real membership from tender_id directly, inside the function,
    where no caller can get it wrong.
    """
    _org_a_id, _org_a_tender_id, _org_a_item_id, actor_id = await _org_tender_and_employee()
    _org_b_id, org_b_tender_id, org_b_item_id, _org_b_actor_id = await _org_tender_and_employee()

    with pytest.raises(knowledge_service.CrossOrganizationTenderError):
        await knowledge_service.set_knowledge_selection(
            org_b_tender_id, org_b_item_id, True, actor_id
        )

    async with UnitOfWork() as uow:
        selections = await uow.knowledge.list_selections_for_tender(org_b_tender_id)
    assert selections == [], "a caller not authorized for org B must not write org B's selection"


async def test_import_employee_master_row_is_idempotent_on_retry():
    """A retried worker job re-parses the same file and re-walks every row
    from row 1 - without the get_import_item_by_source_ref check, the
    second call's create_import_item hits
    uq_knowledge_import_item_source_ref and crashes instead of resuming.
    KNOWN_LIMITATIONS.md: "a partial retry collides with
    UNIQUE(batch_id, source_ref) before resuming."
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Retry Idempotency Org",
    )
    org_id = registered.organization.id
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="employee_master_csv",
            source_document_id=None,
            created_by=registered.user.id,
        )
        await uow.commit()

    row = EmployeeMasterRow(
        row_number=2, employee_code="EMP-01", name="Retry Test Employee", role=None, status=None
    )

    first = await knowledge_service.import_employee_master_row(org_id, batch.id, row)
    second = await knowledge_service.import_employee_master_row(org_id, batch.id, row)

    assert second.id == first.id
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
    assert len(items) == 1, "a retried row must not create a second import_items row"


async def test_import_certification_row_with_blank_employee_ref_routes_to_review():
    """Regression test for KNOWN_LIMITATIONS.md's "a certification row with
    a blank employee cell creates an approved, holderless certification
    instead of routing to review" - fixed via TODO(human) in
    import_certification_row.
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Blank Employee Ref Org",
    )
    org_id = registered.organization.id
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=registered.user.id,
        filename="certifications.xlsx",
        content=b"placeholder",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        side="company",
        doc_type="certification_documents",
    )
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="certification_documents",
            source_document_id=document.id,
            created_by=registered.user.id,
        )
        await uow.commit()

    row = CertificationRow(
        row_number=2,
        cert_key="CERT-BLANK",
        employee_ref=None,
        name="Blank Employee Ref Certification",
        issuer="Issuer",
        issue_date=date(2024, 1, 1),
        expiry_date=None,
    )

    result = await knowledge_service.import_certification_row(
        org_id, batch.id, document.id, row, today=date(2026, 1, 1)
    )

    assert result.status == "needs_review"
    assert result.target_knowledge_item_id is None
    assert "employee" in (result.error_message or "").lower()


async def test_import_certification_row_is_idempotent_on_retry():
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Cert Retry Idempotency Org",
    )
    org_id = registered.organization.id
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=registered.user.id,
        filename="certifications.xlsx",
        content=b"placeholder",
        mime_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        side="company",
        doc_type="certification_documents",
    )
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="certification_documents",
            source_document_id=document.id,
            created_by=registered.user.id,
        )
        await uow.commit()

    row = CertificationRow(
        row_number=2,
        cert_key="CERT-01",
        employee_ref=None,
        name="Retry Test Certification",
        issuer="Issuer",
        issue_date=date(2024, 1, 1),
        expiry_date=None,
    )

    first = await knowledge_service.import_certification_row(
        org_id, batch.id, document.id, row, today=date(2026, 1, 1)
    )
    second = await knowledge_service.import_certification_row(
        org_id, batch.id, document.id, row, today=date(2026, 1, 1)
    )

    assert second.id == first.id
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
    assert len(items) == 1, "a retried row must not create a second import_items row"


async def test_record_batch_parse_failure_gives_the_batch_a_real_terminal_status():
    """Before this fix, a parse failure with zero per-row items created left
    _compute_batch_progress seeing total=0 and reporting "queued" forever -
    indistinguishable from an import that hasn't started.
    KNOWN_LIMITATIONS.md: "a failure before any item row is created leaves
    no terminal state at all."
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Parse Failure Org",
    )
    org_id = registered.organization.id
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="employee_master_csv",
            source_document_id=None,
            created_by=registered.user.id,
        )
        await uow.commit()

    progress_before = await knowledge_service.get_import_batch(batch.id)
    assert progress_before.status == "queued"
    assert progress_before.total_items == 0

    await knowledge_service.record_batch_parse_failure(batch.id, "malformed template: no headers")

    progress_after = await knowledge_service.get_import_batch(batch.id)
    assert progress_after.status == "failed"
    assert progress_after.total_items == 1
    assert progress_after.failed_items == 1


async def test_record_batch_parse_failure_is_idempotent_on_retry():
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Parse Failure Retry Org",
    )
    org_id = registered.organization.id
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id,
            import_type="employee_master_csv",
            source_document_id=None,
            created_by=registered.user.id,
        )
        await uow.commit()

    first = await knowledge_service.record_batch_parse_failure(batch.id, "first attempt")
    second = await knowledge_service.record_batch_parse_failure(batch.id, "retried attempt")

    assert second.id == first.id
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
    assert len(items) == 1, "a retried parse failure must not create a second sentinel row"


async def test_deselecting_also_bumps_state_version():
    # Same call site, opposite direction - selection and deselection are
    # both "knowledge selection change" per Part C's own wording, neither
    # is a no-op for freshness purposes (a deselect is exactly the mutation
    # USED_IN's selected=false property-flip exists to represent in Neo4j).
    _org_id, tender_id, knowledge_item_id, actor_id = await _org_tender_and_employee()
    await knowledge_service.set_knowledge_selection(tender_id, knowledge_item_id, True, actor_id)

    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await knowledge_service.set_knowledge_selection(tender_id, knowledge_item_id, False, actor_id)

    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
    assert after_version == before_version + 1
