"""domain/services/requirement_service.py against real Postgres - extraction
persistence dedup, patch (confirm/edit/reject), merge, split, and the
verify-request outbox write."""

import io
import uuid

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, requirement_service
from tenderos_backend.domain.services.requirement_service import RequirementDraft
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Requirement Service Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Requirement Service Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_chunk_id(tender_id: uuid.UUID, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (document_id, chunk_id) for one real, ingested chunk -
    requirements.source_chunk_id is a NOT NULL FK, so tests need a real row,
    not a random UUID.
    """
    doc = docx.Document()
    doc.add_paragraph("Bidders must hold a valid tax clearance certificate.")
    buf = io.BytesIO()
    doc.save(buf)

    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="annexure.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    assert chunks, "fixture document must produce at least one chunk"
    return document.id, chunks[0].id


async def test_persist_extraction_results_dedups_on_retry():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    draft = RequirementDraft(
        requirement_text="[REQ-001] The bidder must hold a valid tax clearance certificate.",
        category="eligibility",
        mandatory=True,
        source_chunk_id=chunk_id,
        document_id=document_id,
        confidence=0.9,
    )

    first = await requirement_service.persist_extraction_results(tender_id, [draft])
    second = await requirement_service.persist_extraction_results(tender_id, [draft])

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id, "a retried extraction must not create a duplicate row"


async def test_patch_requirement_confirm_sets_human_reviewed():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    draft = RequirementDraft(
        requirement_text="[REQ-002] The bidder must submit three references.",
        category="experience", mandatory=True,
        source_chunk_id=chunk_id, document_id=document_id, confidence=0.8,
    )
    [requirement] = await requirement_service.persist_extraction_results(tender_id, [draft])
    assert requirement.human_reviewed is False

    updated = await requirement_service.patch_requirement(
        requirement.id, org_id, uuid.uuid4()
    )

    assert updated.human_reviewed is True
    assert updated.rejected_at is None


async def test_patch_requirement_reject_soft_deletes_and_is_excluded_by_default():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    draft = RequirementDraft(
        requirement_text="[REQ-003] The bidder must be registered in this jurisdiction.",
        category="eligibility", mandatory=True,
        source_chunk_id=chunk_id, document_id=document_id, confidence=0.7,
    )
    [requirement] = await requirement_service.persist_extraction_results(tender_id, [draft])

    rejected = await requirement_service.patch_requirement(
        requirement.id, org_id, uuid.uuid4(), action="reject", notes="not a real requirement"
    )

    assert rejected.rejected_at is not None
    assert rejected.human_reviewed is True

    visible = await requirement_service.list_requirements_for_tender(tender_id)
    assert rejected.id not in {r.id for r in visible}

    async with UnitOfWork() as uow:
        with_rejected = await uow.requirements.list_for_tender(tender_id, include_rejected=True)
    assert rejected.id in {r.id for r in with_rejected}


async def test_merge_requirements_creates_one_and_deletes_originals():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    drafts = [
        RequirementDraft(
            requirement_text="[REQ-004] The vendor shall provide onsite support.",
            category="technical", mandatory=True,
            source_chunk_id=chunk_id, document_id=document_id, confidence=0.6,
        ),
        RequirementDraft(
            requirement_text="[REQ-005] The vendor shall also provide phone support.",
            category="technical", mandatory=True,
            source_chunk_id=chunk_id, document_id=document_id, confidence=0.6,
        ),
    ]
    originals = await requirement_service.persist_extraction_results(tender_id, drafts)

    merged = await requirement_service.merge_requirements(
        [r.id for r in originals],
        "The vendor shall provide onsite and phone support.",
        "technical",
        True,
        org_id,
        uuid.uuid4(),
    )

    remaining = await requirement_service.list_requirements_for_tender(tender_id)
    remaining_ids = {r.id for r in remaining}
    assert merged.id in remaining_ids
    for original in originals:
        assert original.id not in remaining_ids, "merged-from rows must be gone, not soft-deleted"


async def test_merge_requirements_rejects_cross_tender_ids():
    org_id, tender_a = await _make_tender()
    _, tender_b = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_a, org_id)
    document_id_b, chunk_id_b = await _make_chunk_id(tender_b, org_id)

    [req_a] = await requirement_service.persist_extraction_results(
        tender_a,
        [
            RequirementDraft(
                requirement_text="[REQ-006] Tender A requirement.",
                category="other", mandatory=False,
                source_chunk_id=chunk_id, document_id=document_id, confidence=0.5,
            )
        ],
    )
    [req_b] = await requirement_service.persist_extraction_results(
        tender_b,
        [
            RequirementDraft(
                requirement_text="[REQ-007] Tender B requirement.",
                category="other", mandatory=False,
                source_chunk_id=chunk_id_b, document_id=document_id_b, confidence=0.5,
            )
        ],
    )

    with pytest.raises(requirement_service.CrossTenderOperationError):
        await requirement_service.merge_requirements(
            [req_a.id, req_b.id], "merged", "other", False, org_id, uuid.uuid4()
        )


async def test_split_requirement_creates_multiple_and_deletes_original():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    [original] = await requirement_service.persist_extraction_results(
        tender_id,
        [
            RequirementDraft(
                requirement_text="[REQ-008] Bidders must submit A and must submit B.",
                category="submission", mandatory=True,
                source_chunk_id=chunk_id, document_id=document_id, confidence=0.7,
            )
        ],
    )

    created = await requirement_service.split_requirement(
        original.id,
        [
            {"requirement_text": "Bidders must submit A."},
            {"requirement_text": "Bidders must submit B."},
        ],
        org_id,
        uuid.uuid4(),
    )

    assert len(created) == 2
    remaining = await requirement_service.list_requirements_for_tender(tender_id)
    remaining_ids = {r.id for r in remaining}
    assert original.id not in remaining_ids
    assert all(r.id in remaining_ids for r in created)
    assert all(r.human_reviewed for r in created)


async def test_request_verification_writes_an_outbox_event():
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    [requirement] = await requirement_service.persist_extraction_results(
        tender_id,
        [
            RequirementDraft(
                requirement_text="[REQ-009] The bidder shall comply with data protection law.",
                category="security", mandatory=True,
                source_chunk_id=chunk_id, document_id=document_id, confidence=0.9,
            )
        ],
    )

    await requirement_service.request_verification(requirement.id)

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == requirement.id)
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].event_type == "RequirementVerificationRequested"
