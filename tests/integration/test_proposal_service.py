"""domain/services/proposal_service.py against real Postgres - the grounded
context builder, persist_section_and_claims' idempotency and versioning,
the state_version bump, get_proposal_trace, and update_section_content's
audit trail. enforce_claim_grounding's own policy is pinned separately in
tests/unit/test_claim_grounding.py; these tests exercise it end-to-end
against real persisted rows rather than re-pinning the same rules."""

import io
import uuid

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, proposal_service
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_chunk_id(tender_id: uuid.UUID, org_id: uuid.UUID) -> uuid.UUID:
    doc = docx.Document()
    doc.add_paragraph("The company must demonstrate three prior telecom migrations.")
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


async def _make_approved_requirement_with_evidence(
    tender_id: uuid.UUID,
    org_id: uuid.UUID,
    *,
    category: str = "experience",
    compliance_status: str = "fully_compliant",
) -> tuple[uuid.UUID, uuid.UUID]:
    """A requirement with a human_approved compliance_entries row and one
    verified/valid evidence_link attached - the minimal real shape
    _facts_from_compliance looks for. Returns (requirement_id, entry_id).
    """
    chunk_id = await _make_chunk_id(tender_id, org_id)
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="The company must demonstrate three prior telecom migrations.",
            normalized_text="the company must demonstrate three prior telecom migrations",
            category=category,
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=str(uuid.uuid4()),
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status=compliance_status
        )
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        evidence = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="past_project",
            content_fingerprint=str(uuid.uuid4()),
            source_chunk_id=chunk_id,
            excerpt="Delivered three telecom network migrations for prior clients.",
            verified=True,
            validity_status="valid",
        )
        await uow.compliance.link_evidence(entry.id, evidence.id)
        await uow.commit()
        return requirement.id, entry.id


def _section_draft(claims: list[ProposalClaimDraft]) -> ProposalSectionDraft:
    return ProposalSectionDraft(content="Drafted section content.", claims=claims)


async def test_facts_from_compliance_only_includes_human_approved_entries():
    org_id, tender_id = await _make_org_and_tender("Grounded Context Test")
    requirement_id, _ = await _make_approved_requirement_with_evidence(org_id=org_id, tender_id=tender_id)

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_compliance(uow, tender_id, frozenset({"experience"}))

    requirement_facts = [f for f in facts if f.kind == "requirement"]
    evidence_facts = [f for f in facts if f.kind == "evidence"]
    assert [f.requirement_id for f in requirement_facts] == [requirement_id]
    assert len(evidence_facts) == 1


async def test_facts_from_compliance_excludes_unapproved_entries():
    org_id, tender_id = await _make_org_and_tender("Unapproved Exclusion Test")
    chunk_id = await _make_chunk_id(tender_id, org_id)
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-002",
            requirement_text="Unapproved requirement.",
            normalized_text="unapproved requirement",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=str(uuid.uuid4()),
        )
        # human_approved defaults False - never explicitly approved.
        await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_compliance(uow, tender_id, frozenset({"experience"}))

    assert facts == []


async def test_persist_section_and_claims_creates_grounded_claim_with_linked_evidence():
    org_id, tender_id = await _make_org_and_tender("Grounded Claim Test")
    # Build prepared facts from a real evidence_link row, so the
    # evidence_link_id resolves to something that actually exists.
    _, entry_id = await _make_approved_requirement_with_evidence(
        tender_id=tender_id, org_id=org_id, category="technical"
    )
    async with UnitOfWork() as uow:
        real_links = await uow.compliance.list_evidence_for_compliance_entry(entry_id)
    prepared = [
        proposal_service.PreparedFact(
            label="evidence_1",
            kind="evidence",
            excerpt="Delivered three telecom migrations.",
            evidence_link_id=real_links[0].id,
        )
    ]
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="The company completed three telecom migrations.",
                claim_type="experience",
                is_factual=True,
                evidence_refs=["evidence_1"],
            )
        ]
    )

    section = await proposal_service.persist_section_and_claims(
        tender_id, "Relevant Experience", draft, prepared, {}
    )

    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
        assert len(claims) == 1
        assert claims[0].grounding_status == "grounded"
        evidence_links = await uow.proposal.list_evidence_for_claim(claims[0].id)
        assert len(evidence_links) == 1
        assert evidence_links[0].evidence_link_id == real_links[0].id


async def test_persist_section_and_claims_retry_does_not_duplicate_claims():
    org_id, tender_id = await _make_org_and_tender("Claim Retry Test")
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="General narrative statement about the company.",
                claim_type="capability",
                is_factual=False,
            )
        ]
    )

    first = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )
    second = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )

    assert first.id == second.id, "identical retry must reuse the same 'draft'-status section, not fork a new version"
    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(second.id)
    assert len(claims) == 1, "retry must not duplicate the same claim"


async def test_persist_section_and_claims_bumps_state_version_only_on_real_change():
    org_id, tender_id = await _make_org_and_tender("State Version Bump Test")
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="A narrative claim.",
                claim_type="other",
                is_factual=False,
            )
        ]
    )
    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await proposal_service.persist_section_and_claims(tender_id, "Company Overview", draft, [], {})
    async with UnitOfWork() as uow:
        after_first = (await uow.tenders.get(tender_id)).state_version
    assert after_first == before_version + 1

    # Identical retry: no new/changed claim, so no further bump.
    await proposal_service.persist_section_and_claims(tender_id, "Company Overview", draft, [], {})
    async with UnitOfWork() as uow:
        after_second = (await uow.tenders.get(tender_id)).state_version
    assert after_second == after_first, "a pure retry with nothing changed must not bump state_version again"


async def test_factual_claim_in_company_overview_with_no_evidence_is_unsupported_not_not_required():
    # The specific case flagged during planning: Company Overview is the one
    # section template with no pre-gated approved data behind it, which is
    # exactly where an unenforced narrative exemption could let an
    # ungrounded factual claim slip through as "not_required" instead of
    # honestly "unsupported". enforce_claim_grounding takes no section name
    # at all, so this proves the guarantee holds through persist_section_
    # and_claims end-to-end, not just at the unit level.
    org_id, tender_id = await _make_org_and_tender("Company Overview Grounding Test")
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="The company has delivered over 200 successful projects.",
                claim_type="capability",
                is_factual=True,
                evidence_refs=[],
            )
        ]
    )

    section = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )

    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
    assert claims[0].grounding_status == "unsupported"
    assert claims[0].grounding_status != "not_required"


async def test_materialize_claim_links_drops_a_label_not_in_this_sections_own_facts():
    # There is no code path by which a claim can resolve a label to a real
    # row from outside this section's own prepared fact list - a label from
    # another section, another tender, or an invented one all resolve to
    # nothing, the same "skip rather than fabricate" posture as
    # materialize_evidence. This is what makes "evidence from another
    # organization rejected" structurally true rather than merely tested.
    claim = ProposalClaimDraft(
        claim_text="Some claim citing a label that was never offered.",
        claim_type="experience",
        is_factual=True,
        evidence_refs=["evidence_99"],
    )

    requirement_ids, evidence_ids, employee_ids = await proposal_service.materialize_claim_links(
        [], claim
    )

    assert requirement_ids == []
    assert evidence_ids == []
    assert employee_ids == []


async def test_get_proposal_trace_walks_claim_to_evidence_to_source():
    org_id, tender_id = await _make_org_and_tender("Trace Test")
    _, entry_id = await _make_approved_requirement_with_evidence(tender_id=tender_id, org_id=org_id)
    async with UnitOfWork() as uow:
        real_links = await uow.compliance.list_evidence_for_compliance_entry(entry_id)
    prepared = [
        proposal_service.PreparedFact(
            label="evidence_1",
            kind="evidence",
            excerpt="Delivered three telecom migrations.",
            evidence_link_id=real_links[0].id,
        )
    ]
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="The company completed three telecom migrations.",
                claim_type="experience",
                is_factual=True,
                evidence_refs=["evidence_1"],
            )
        ]
    )
    section = await proposal_service.persist_section_and_claims(
        tender_id, "Relevant Experience", draft, prepared, {}
    )
    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)

    trace = await proposal_service.get_proposal_trace(claims[0].id)

    assert trace.grounding_status == "grounded"
    assert trace.section_name == "Relevant Experience"
    assert len(trace.evidence) == 1
    assert trace.evidence[0].evidence_link_id == real_links[0].id


async def test_update_section_content_writes_audit_event():
    org_id, tender_id = await _make_org_and_tender("Section Edit Test")
    draft = _section_draft([])
    section = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )
    actor_id = uuid.uuid4()

    updated = await proposal_service.update_section_content(
        section.id, "Human-edited content.", actor_id
    )

    assert updated.content == "Human-edited content."
    assert updated.status == "review"
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "proposal_section", AuditEvent.target_id == section.id
            )
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].action == "proposal_section_edit"
    assert events[0].actor_id == str(actor_id)
