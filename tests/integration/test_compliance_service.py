"""domain/services/compliance_service.py against real Postgres - evidence
materialization idempotency, compliance-entry persistence idempotency
(B7 stop-gate: "retry does not duplicate evidence or compliance"),
clarification generation, human override/approve auditing, and the
traceability walk."""

import io
import uuid
from datetime import date

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.knowledge.retrieval import KnowledgeSearchResult
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.domain.services.compliance_service import (
    approve_compliance_entry,
    enforce_evidence_sufficiency,
    get_compliance_trace,
    materialize_evidence,
    override_compliance_entry,
    persist_compliance_decision,
    prepare_evidence_candidates,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Compliance Service Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Compliance Service Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_requirement_chunk(tender_id: uuid.UUID, org_id: uuid.UUID) -> uuid.UUID:
    doc = docx.Document()
    doc.add_paragraph("Bidders must hold a valid data protection certification.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="requirement.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    return chunks[0].id


async def _make_requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="Bidders must hold a valid data protection certification.",
            normalized_text="bidders must hold a valid data protection certification",
            category="eligibility",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=str(uuid.uuid4()),
        )
        await uow.commit()
        return requirement.id


async def _make_evidence_knowledge_item(org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Real knowledge_items + document_chunks rows, since evidence_links'
    FKs need real rows to point at - a fabricated UUID would violate the
    FK constraint at INSERT time, which is exactly the kind of thing this
    session's discipline is to catch by testing against real Postgres.
    """
    doc = docx.Document()
    doc.add_paragraph("ISO 27001 certification, issued 2024, covering data protection controls.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="cert.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="certification_documents",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Placeholder Cert Holder",
            content_fingerprint=str(uuid.uuid4()),
            title="ISO 27001 Certification",
        )
        await uow.knowledge.update_knowledge_item(knowledge_item.id, source_chunk_id=chunks[0].id)
        await uow.commit()
    return knowledge_item.id, chunks[0].id


def _search_result(knowledge_item_id: uuid.UUID, chunk_id: uuid.UUID) -> KnowledgeSearchResult:
    return KnowledgeSearchResult(
        knowledge_item_id=knowledge_item_id,
        item_type="employee",
        title="ISO 27001 Certification",
        excerpt="ISO 27001 certification, issued 2024, covering data protection controls.",
        document_id=uuid.uuid4(),
        chunk_id=chunk_id,
        page_num=None,
        heading_path=None,
        fusion_score=0.5,
        rerank_score=0.9,
    )


async def test_materialize_evidence_is_idempotent_on_retry():
    org_id, tender_id = await _make_tender()
    knowledge_item_id, chunk_id = await _make_evidence_knowledge_item(org_id)
    results = [_search_result(knowledge_item_id, chunk_id)]
    prepared = await prepare_evidence_candidates(results, today=date.today())

    first = await materialize_evidence(tender_id, prepared, ["evidence_1"])
    second = await materialize_evidence(tender_id, prepared, ["evidence_1"])

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].id == second[0].id, "a retried materialization must not create a duplicate row"


async def test_persist_compliance_decision_is_idempotent_on_retry():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)

    proposal = ComplianceProposal(
        status="evidence_unavailable",
        evidence_refs=[],
        confidence=0.4,
        reasoning_summary="No supporting evidence was retrieved.",
    )
    sufficiency = enforce_evidence_sufficiency(proposal, [])

    first = await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    second = await persist_compliance_decision(requirement_id, proposal, sufficiency, [])

    assert first.id == second.id, "a retried compliance decision must update in place, not duplicate"
    async with UnitOfWork() as uow:
        entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    assert len(entries) == 1


async def test_persist_compliance_decision_generates_clarification_once():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)

    proposal = ComplianceProposal(
        status="partially_compliant",
        evidence_refs=[],
        confidence=0.6,
        reasoning_summary="Partial match.",
        clarification_required=True,
        clarification_question="Does the certification cover the full required scope?",
    )
    sufficiency = enforce_evidence_sufficiency(proposal, [])

    await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    await persist_compliance_decision(requirement_id, proposal, sufficiency, [])

    async with UnitOfWork() as uow:
        clarifications = await uow.requirements.list_clarification_items_for_tender(tender_id)
    assert len(clarifications) == 1
    assert clarifications[0].requirement_id == requirement_id


async def test_approve_compliance_entry_writes_audit_event():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    proposal = ComplianceProposal(
        status="evidence_unavailable", evidence_refs=[], confidence=0.3, reasoning_summary="x"
    )
    entry = await persist_compliance_decision(
        requirement_id, proposal, enforce_evidence_sufficiency(proposal, []), []
    )
    actor_id = uuid.uuid4()

    updated = await approve_compliance_entry(entry.id, actor_id)

    assert updated.human_approved is True
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "compliance_entry", AuditEvent.target_id == entry.id
            )
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].action == "compliance_approve"
    assert events[0].actor_id == str(actor_id)


async def test_override_compliance_entry_writes_audit_event_and_sets_status():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    proposal = ComplianceProposal(
        status="evidence_unavailable", evidence_refs=[], confidence=0.3, reasoning_summary="x"
    )
    entry = await persist_compliance_decision(
        requirement_id, proposal, enforce_evidence_sufficiency(proposal, []), []
    )
    actor_id = uuid.uuid4()

    updated = await override_compliance_entry(
        entry.id, actor_id, status="non_compliant", reasoning_summary="Reviewer confirmed a gap."
    )

    assert updated.status == "non_compliant"
    assert updated.reasoning_summary == "Reviewer confirmed a gap."
    assert updated.human_approved is True
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "compliance_entry", AuditEvent.target_id == entry.id
            )
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].action == "compliance_override"
    assert events[0].before["status"] == "evidence_unavailable"
    assert events[0].after["status"] == "non_compliant"


async def test_get_compliance_trace_walks_requirement_to_evidence_source():
    org_id, tender_id = await _make_tender()
    req_chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, req_chunk_id)
    knowledge_item_id, evidence_chunk_id = await _make_evidence_knowledge_item(org_id)

    results = [_search_result(knowledge_item_id, evidence_chunk_id)]
    prepared = await prepare_evidence_candidates(results, today=date.today())
    materialized = await materialize_evidence(tender_id, prepared, ["evidence_1"])

    proposal = ComplianceProposal(
        status="fully_compliant",
        evidence_refs=["evidence_1"],
        confidence=0.9,
        reasoning_summary="The certification directly covers this requirement.",
    )
    sufficiency = enforce_evidence_sufficiency(proposal, materialized)
    entry = await persist_compliance_decision(requirement_id, proposal, sufficiency, materialized)

    trace = await get_compliance_trace(entry.id)

    assert trace.requirement_id == requirement_id
    assert trace.compliance_entry_id == entry.id
    assert len(trace.evidence) == 1
    assert trace.evidence[0].knowledge_item_id == knowledge_item_id
    assert trace.evidence[0].document_filename == "cert.docx"
