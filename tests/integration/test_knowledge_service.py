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

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.models.knowledge import KnowledgeItem
from tenderos_backend.db.models.outbox import OutboxEvent
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


async def test_set_knowledge_selection_writes_a_knowledge_selection_changed_outbox_event():
    # B14: the outbox row this project's own reanalysis job (apps/worker/
    # jobs/reanalysis.py) is dispatched from. Asserted directly against the
    # real row, not just "the call didn't raise" - the same standard this
    # file's own state_version test already holds itself to.
    _org_id, tender_id, knowledge_item_id, actor_id = await _org_tender_and_employee()

    updated = await knowledge_service.set_knowledge_selection(
        tender_id, knowledge_item_id, True, actor_id
    )

    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == updated.id)
        )
    events = result.scalars().all()
    assert len(events) == 1
    event = events[0]
    assert event.aggregate_type == "knowledge_selection"
    assert event.event_type == "KnowledgeSelectionChanged"
    assert event.payload == {
        "tender_id": str(tender_id),
        "knowledge_item_id": str(knowledge_item_id),
        "selected": True,
        "state_version": tender.state_version,
    }
    assert (
        event.idempotency_key == f"knowledge_selection_changed:{tender_id}:{tender.state_version}"
    )


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
    (
        _other_org_id,
        _other_tender_id,
        other_org_item_id,
        _other_actor_id,
    ) = await _org_tender_and_employee()

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


async def test_import_employee_master_row_concurrent_delivery_converges_not_crashes():
    """B16: the test directly above proves SEQUENTIAL retry-safety (a
    worker resuming a crashed batch, one call fully finishing before the
    next starts) - it says nothing about two deliveries genuinely
    overlapping (two RQ workers, or a retry racing a still-running
    original attempt). asyncio.gather against two real service calls, the
    same technique that proved the document_service.create_document and
    idempotency.py races real, is what actually tests that. Converges to
    ONE import_items row and ONE real employee knowledge_item - not just
    "neither call raised."
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Concurrent Import Org",
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
        row_number=2,
        employee_code="EMP-CONCURRENT",
        name="Concurrent Test Employee",
        role=None,
        status=None,
    )

    first, second = await asyncio.gather(
        knowledge_service.import_employee_master_row(org_id, batch.id, row),
        knowledge_service.import_employee_master_row(org_id, batch.id, row),
    )

    assert first.status == "approved" and second.status == "approved"
    assert first.target_knowledge_item_id == second.target_knowledge_item_id, (
        "both concurrent attempts must converge on the SAME employee knowledge_item, not create two"
    )
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
    assert len(items) == 1, "a genuine race must not create a second import_items row either"


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
        mime_type=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
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


async def test_import_certification_row_concurrent_delivery_converges_not_crashes():
    """Same concurrency gap as import_employee_master_row's own sibling
    test above, for this file's second select-then-insert site. A real
    employee_ref (not the blank-ref review path) so this actually
    exercises create_certification's own fingerprint-keyed convergence,
    not just the import_items row.
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Cert Concurrent Org",
    )
    org_id = registered.organization.id
    async with UnitOfWork() as uow:
        _knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Cert Concurrent Holder",
            content_fingerprint=uuid.uuid4().hex,
            title="Cert Concurrent Holder",
            employee_code="EMP-CONCURRENT-CERT",
        )
        await uow.commit()
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
        cert_key="CERT-CONCURRENT",
        employee_ref="EMP-CONCURRENT-CERT",
        name="Concurrent Test Certification",
        issuer="Issuer",
        issue_date=date(2024, 1, 1),
        expiry_date=None,
    )

    first, second = await asyncio.gather(
        knowledge_service.import_certification_row(
            org_id, batch.id, document.id, row, today=date(2026, 1, 1)
        ),
        knowledge_service.import_certification_row(
            org_id, batch.id, document.id, row, today=date(2026, 1, 1)
        ),
    )

    assert first.status == "approved" and second.status == "approved"
    assert first.target_knowledge_item_id == second.target_knowledge_item_id, (
        "both concurrent attempts must converge on the SAME certification knowledge_item"
    )
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
    assert len(items) == 1


async def test_uq_employee_code_per_org_is_enforced_at_the_database_level():
    """B16's own partial-index audit (KNOWN_LIMITATIONS.md): migration
    005_knowledge.py created uq_employee_code_per_org directly via
    op.create_index, never mirrored on the Employee model - meaning
    Base.metadata.create_all() (every pytest test database) silently
    lacked it until now. Proves the invariant itself, independent of
    resolve_employee_duplicate's own application-level dedup (a separate
    mechanism, tested elsewhere) - two direct repository-level creates,
    same org + employee_code, different content_fingerprint so that
    unrelated constraint can't be what fires instead.
    """
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Employee Code Uniqueness Org",
    )
    org_id = registered.organization.id

    async with UnitOfWork() as uow:
        await uow.knowledge.create_employee(
            organization_id=org_id,
            name="First Employee",
            content_fingerprint=uuid.uuid4().hex,
            title="First Employee",
            employee_code="EMP-DUP",
        )
        await uow.commit()

    with pytest.raises(IntegrityError):
        async with UnitOfWork() as uow:
            await uow.knowledge.create_employee(
                organization_id=org_id,
                name="Second Employee",
                content_fingerprint=uuid.uuid4().hex,
                title="Second Employee",
                employee_code="EMP-DUP",
            )
            await uow.commit()


async def _org_and_resume_batch() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns (org_id, user_id, batch_id) for a bulk_resumes batch - shared
    setup for the import_resume/knowledge_item_chunks tests below."""
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password="hunter2hunter2",
        organization_name="Resume Import Org",
    )
    org_id = registered.organization.id
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id, import_type="bulk_resumes", created_by=registered.user.id
        )
        await uow.commit()
    return org_id, registered.user.id, batch.id


async def _resume_document_with_chunks(
    org_id: uuid.UUID, user_id: uuid.UUID, filename: str, texts: list[str]
):
    """A real document + real document_chunks rows, same shape a real
    résumé produces after B4 chunking (summary/experience/certifications/
    education as separate chunks) - no embedding passed, matching every
    other create_chunk call site in this test suite (test_compliance_routes,
    test_graph_routes, test_gate_target_service, test_graph_projection).
    content must be unique per call - document_service.create_document
    dedupes by content hash within an org (its own docstring: "two uploads
    of the same bytes land on the same key"), so two placeholder-content
    calls in the same org/test would silently collapse into one document
    row and collide on uq_document_chunk_index. filename goes into the
    placeholder bytes to guarantee that.
    """
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=filename,
        content=f"placeholder:{filename}".encode(),
        mime_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        side="company",
        doc_type="resume",
    )
    chunks = []
    async with UnitOfWork() as uow:
        for i, text in enumerate(texts):
            chunk = await uow.documents.create_chunk(
                document_id=document.id,
                chunk_index=i,
                chunk_text=text,
                normalized_text=text.lower(),
                content_hash=uuid.uuid4().hex,
            )
            chunks.append(chunk)
        await uow.commit()
    return document, chunks


async def test_import_resume_associates_every_resume_chunk_with_the_new_employee():
    """This is the join-table fix's core claim: an employee's knowledge_item
    must be findable from ANY of its résumé's chunks, not just one - the
    scalar knowledge_items.source_chunk_id this employee never had could
    only ever have pointed at one, which is exactly the bug B16's citation-
    correctness investigation traced (docs/eval/
    citation-correctness-investigation.md).
    """
    org_id, user_id, batch_id = await _org_and_resume_batch()
    _document, chunks = await _resume_document_with_chunks(
        org_id,
        user_id,
        "candidate.docx",
        ["Summary: senior engineer.", "Certifications: PMP, PRINCE2."],
    )
    profile = ExtractedEmployeeProfile(name="Chunk Association Candidate", confidence=0.9)

    item = await knowledge_service.import_resume(
        org_id, batch_id, "candidate.docx", _document.id, profile, [c.id for c in chunks]
    )
    assert item.target_knowledge_item_id is not None

    async with UnitOfWork() as uow:
        for chunk in chunks:
            found = await uow.knowledge.get_knowledge_items_by_chunk_id(chunk.id)
            assert [f.id for f in found] == [item.target_knowledge_item_id], (
                f"chunk {chunk.id} (index {chunk.chunk_index}) did not resolve back to the employee"
            )


async def test_import_resume_concurrent_delivery_converges_not_crashes():
    """Same concurrency gap as the other two sites' own sibling tests, for
    this file's third select-then-insert site - and the one that, unlike
    the other two, had NO retry-resume guard at all before this pass
    (import_resume went straight to create_import_item, no
    get_import_item_by_source_ref check first - see the function's own
    updated docstring). Two concurrent deliveries of the identical résumé
    (same source_ref, same document, same chunks - the real "two RQ
    workers picked up the same knowledge_document_uploaded event" shape)
    must converge on one employee, not crash and not create two.
    """
    org_id, user_id, batch_id = await _org_and_resume_batch()
    _document, chunks = await _resume_document_with_chunks(
        org_id,
        user_id,
        "concurrent-candidate.docx",
        ["Summary: concurrent delivery test candidate."],
    )
    profile = ExtractedEmployeeProfile(name="Concurrent Delivery Candidate", confidence=0.9)

    first, second = await asyncio.gather(
        knowledge_service.import_resume(
            org_id,
            batch_id,
            "concurrent-candidate.docx",
            _document.id,
            profile,
            [c.id for c in chunks],
        ),
        knowledge_service.import_resume(
            org_id,
            batch_id,
            "concurrent-candidate.docx",
            _document.id,
            profile,
            [c.id for c in chunks],
        ),
    )

    assert first.status == "approved" and second.status == "approved"
    assert first.target_knowledge_item_id == second.target_knowledge_item_id, (
        "both concurrent deliveries must converge on the SAME employee knowledge_item"
    )
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch_id)
    assert len(items) == 1, "a genuine race must not create a second import_items row"


async def test_import_resume_duplicate_merge_adds_new_chunks_without_dropping_old_ones():
    """The duplicate-merge branch is additive, not a replace - a second
    résumé upload for the same person adds its chunks to whatever the first
    upload already associated. Real, accepted consequence (see this
    function's own inline comment in knowledge_service.py and
    KNOWN_LIMITATIONS.md): a chunk from an OLDER résumé stays associated
    even if the newer one no longer repeats that claim.
    """
    org_id, user_id, batch_id = await _org_and_resume_batch()
    _first_doc, first_chunks = await _resume_document_with_chunks(
        org_id, user_id, "v1.docx", ["Summary: engineer."]
    )
    _second_doc, second_chunks = await _resume_document_with_chunks(
        org_id, user_id, "v2.docx", ["Certifications: PMP."]
    )
    # Same name + same email is a strong identity signal (dedup.py) - the
    # second call resolves DUPLICATE against the first, not NEW.
    profile = ExtractedEmployeeProfile(
        name="Merge Candidate", email="merge.candidate@example.com", confidence=0.9
    )

    first = await knowledge_service.import_resume(
        org_id, batch_id, "v1.docx", _first_doc.id, profile, [c.id for c in first_chunks]
    )
    second = await knowledge_service.import_resume(
        org_id, batch_id, "v2.docx", _second_doc.id, profile, [c.id for c in second_chunks]
    )

    assert second.target_knowledge_item_id == first.target_knowledge_item_id

    async with UnitOfWork() as uow:
        old_chunk_hits = await uow.knowledge.get_knowledge_items_by_chunk_id(first_chunks[0].id)
        new_chunk_hits = await uow.knowledge.get_knowledge_items_by_chunk_id(second_chunks[0].id)
    assert [h.id for h in old_chunk_hits] == [first.target_knowledge_item_id]
    assert [h.id for h in new_chunk_hits] == [first.target_knowledge_item_id]


async def test_get_knowledge_items_by_chunk_id_still_resolves_certifications_via_the_scalar_column():
    """Regression guard: get_knowledge_items_by_chunk_id's new OR condition
    (against knowledge_item_chunks) must not disturb the existing, proven
    scalar-column path certifications/past_projects/capabilities rely on.
    """
    org_id, user_id, _batch_id = await _org_and_resume_batch()
    _document, chunks = await _resume_document_with_chunks(
        org_id, user_id, "cert-source.docx", ["PMP, valid through 2027."]
    )

    async with UnitOfWork() as uow:
        knowledge_item, _certification = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="PMP",
            content_fingerprint=uuid.uuid4().hex,
            source_chunk_id=chunks[0].id,
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        found = await uow.knowledge.get_knowledge_items_by_chunk_id(chunks[0].id)
    assert [f.id for f in found] == [knowledge_item.id]


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


async def test_one_knowledge_item_is_reused_across_two_tenders_and_deselection_stays_per_tender():
    """B17 Phase 0, Part N (Build Guide :3920-3921): one knowledge fact can
    serve many tenders, and deselecting it in one leaves the others as they
    were. The only prior evidence was graph-layer
    (test_graph_projection.py::test_deselecting_fact_in_one_tender_does_not_
    affect_another), which creates each tender under a NEW organization and
    writes selections through a helper, not this service. This test is the
    service-level observation: ONE organization, TWO of its tenders, ONE
    knowledge_items row, every selection change made through
    set_knowledge_selection exactly as the route makes it.
    """
    org_id, tender_a, knowledge_item_id, actor_id = await _org_tender_and_employee()
    tender_b = (
        await create_tender(organization_id=org_id, name="Second Tender", created_by=actor_id)
    ).id

    # Reuse: the same item selected into both tenders of the same org.
    await knowledge_service.set_knowledge_selection(tender_a, knowledge_item_id, True, actor_id)
    await knowledge_service.set_knowledge_selection(tender_b, knowledge_item_id, True, actor_id)

    async with UnitOfWork() as uow:
        selected_a = await uow.knowledge.list_selections_for_tender(tender_a, selected_only=True)
        selected_b = await uow.knowledge.list_selections_for_tender(tender_b, selected_only=True)
        org_items = (
            (
                await uow.session.execute(
                    select(KnowledgeItem.id).where(KnowledgeItem.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        version_b_before = (await uow.tenders.get(tender_b)).state_version
    assert [s.knowledge_item_id for s in selected_a] == [knowledge_item_id]
    assert [s.knowledge_item_id for s in selected_b] == [knowledge_item_id]
    assert org_items == [knowledge_item_id], (
        "reuse means one knowledge_items row shared by both tenders, not a per-tender copy"
    )

    # Isolation: deselect in tender A only.
    await knowledge_service.set_knowledge_selection(tender_a, knowledge_item_id, False, actor_id)

    async with UnitOfWork() as uow:
        all_a = await uow.knowledge.list_selections_for_tender(tender_a)
        all_b = await uow.knowledge.list_selections_for_tender(tender_b)
        item = await uow.session.get(KnowledgeItem, knowledge_item_id)
        version_b_after = (await uow.tenders.get(tender_b)).state_version
    assert [(s.knowledge_item_id, s.selected) for s in all_a] == [(knowledge_item_id, False)]
    assert [(s.knowledge_item_id, s.selected) for s in all_b] == [(knowledge_item_id, True)]
    assert all_b[0].deselected_at is None
    assert version_b_after == version_b_before, (
        "a deselection in tender A must not bump tender B's state_version"
    )
    assert item is not None, "deselecting in one tender must not delete the shared item"
