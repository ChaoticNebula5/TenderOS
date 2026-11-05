"""domain/services/proposal_service.py against real Postgres - the grounded
context builder, persist_section_and_claims' idempotency and versioning,
the state_version bump, get_proposal_trace, update_section_content's
audit trail, and reground_claim (B14) against a real drafted claim.
enforce_claim_grounding's own policy is pinned separately in
tests/unit/test_claim_grounding.py; these tests exercise it end-to-end
against real persisted rows rather than re-pinning the same rules."""

import io
import uuid
from pathlib import Path

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import EvidenceLink
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    commercial_service,
    document_service,
    proposal_service,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio

_PRICING_FIXTURES_DIR = (
    Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"
)


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
    # Pinned exactly (not just kind/count) - this is the drafting node's own
    # grounded-context input, about to be refactored (evidence-gathering
    # shared with _facts_from_team, both reused by get_proposal_trace's new
    # currently_in_scope_evidence). This test is the "green before, green
    # after, unchanged" proof that refactor doesn't change - run before AND
    # after the refactor with no edits.
    org_id, tender_id = await _make_org_and_tender("Grounded Context Test")
    requirement_id, _ = await _make_approved_requirement_with_evidence(
        org_id=org_id, tender_id=tender_id
    )

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_compliance(
            uow, tender_id, frozenset({"experience"})
        )

    requirement_facts = [f for f in facts if f.kind == "requirement"]
    evidence_facts = [f for f in facts if f.kind == "evidence"]
    assert len(requirement_facts) == 1
    assert requirement_facts[0].requirement_id == requirement_id
    assert requirement_facts[0].label == "requirement_1"
    assert requirement_facts[0].excerpt == (
        "The company must demonstrate three prior telecom migrations."
    )
    assert len(evidence_facts) == 1
    assert evidence_facts[0].label == "evidence_1"
    assert evidence_facts[0].excerpt == (
        "Delivered three telecom network migrations for prior clients."
    )
    assert evidence_facts[0].requirement_id is None
    assert evidence_facts[0].employee_knowledge_item_id is None


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
        facts = await proposal_service._facts_from_compliance(
            uow, tender_id, frozenset({"experience"})
        )

    assert facts == []


async def test_facts_from_team_only_includes_human_selected_matches():
    # Same pinning purpose as test_facts_from_compliance_only_includes_
    # human_approved_entries above, for the other half of the refactor.
    # Proposed Team claims are one of the two categories this project's own
    # (now-superseded) citation hypothesis named as a live candidate cause -
    # this half of the refactor is the one most relevant to that open
    # question, so it gets pinned by hand, not left to the compliance test
    # alone to stand in for both.
    org_id, tender_id = await _make_org_and_tender("Team Facts Test")
    chunk_id = await _make_chunk_id(tender_id, org_id)
    async with UnitOfWork() as uow:
        knowledge_item, employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Dana Whitfield",
            content_fingerprint=str(uuid.uuid4()),
            title="Dana Whitfield",
            current_title="Senior Network Engineer",
        )
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Network Engineer", content_fingerprint=str(uuid.uuid4())
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=knowledge_item.id
        )
        await uow.talent.update_role_match(match.id, human_selected=True)
        evidence = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="employee",
            content_fingerprint=str(uuid.uuid4()),
            source_chunk_id=chunk_id,
            excerpt="Led network engineering on three prior telecom migrations.",
            verified=True,
            validity_status="valid",
        )
        await uow.talent.link_role_match_evidence(match.id, evidence.id, "technical_fit")
        await uow.commit()

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_team(uow, tender_id)

    employee_facts = [f for f in facts if f.kind == "employee"]
    evidence_facts = [f for f in facts if f.kind == "evidence"]
    assert len(employee_facts) == 1
    assert employee_facts[0].employee_knowledge_item_id == knowledge_item.id
    assert employee_facts[0].label == "employee_1"
    assert employee_facts[0].excerpt == (
        "Senior Network Engineer, selected for role: Network Engineer"
    )
    assert len(evidence_facts) == 1
    assert evidence_facts[0].label == "evidence_1"
    assert evidence_facts[0].excerpt == (
        "Led network engineering on three prior telecom migrations."
    )
    assert evidence_facts[0].requirement_id is None
    assert evidence_facts[0].employee_knowledge_item_id is None


async def test_facts_from_team_excludes_unselected_matches():
    org_id, tender_id = await _make_org_and_tender("Team Facts Unselected Test")
    async with UnitOfWork() as uow:
        knowledge_item, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Unselected Candidate",
            content_fingerprint=str(uuid.uuid4()),
            title="Engineer",
        )
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Engineer", content_fingerprint=str(uuid.uuid4())
        )
        # human_selected defaults False - never explicitly selected.
        await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_team(uow, tender_id)

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

    assert first.id == second.id, (
        "identical retry must reuse the same 'draft'-status section, not fork a new version"
    )
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
    assert after_second == after_first, (
        "a pure retry with nothing changed must not bump state_version again"
    )


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
    assert claims[0].claim_kind == "current"


async def test_commitment_claim_with_no_evidence_is_unsupported_not_exempt():
    # KNOWN_LIMITATIONS.md's [B9/B16] entry, resolved: a forward-looking
    # commitment ("will support SSO") is_factual=true with no evidence must
    # stay exactly as visible as a "current" factual claim in the same
    # position - claim_kind is a measurement-bucket tag, not a second way
    # to reach "not_required". If this ever regressed to auto-exempting
    # commitments, it would silently hide an unbacked promise, which the
    # user's own stated priority for this fix explicitly rules out.
    org_id, tender_id = await _make_org_and_tender("Commitment Grounding Test")
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="We will support SSO within the first quarter of the contract.",
                claim_type="capability",
                is_factual=True,
                claim_kind="commitment",
                evidence_refs=[],
            )
        ]
    )

    section = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )

    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
    assert claims[0].claim_kind == "commitment"
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


async def test_get_proposal_trace_shows_currently_in_scope_evidence_for_unsupported_claim():
    # The concrete "thin vs. uncited" case this whole feature exists to
    # distinguish: a real approved compliance entry + verified evidence
    # link exists in this claim's own section, but the claim itself cites
    # nothing (evidence_refs=[]). Before this change, get_proposal_trace's
    # only evidence source was the junction rows a claim actually cites -
    # zero for an unsupported claim by definition - so this case rendered
    # as an unexplained empty list, indistinguishable from evidence
    # genuinely never having existed.
    org_id, tender_id = await _make_org_and_tender("Uncited Evidence Test")
    _, entry_id = await _make_approved_requirement_with_evidence(
        tender_id=tender_id, org_id=org_id, category="experience"
    )
    async with UnitOfWork() as uow:
        real_links = await uow.compliance.list_evidence_for_compliance_entry(entry_id)
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="The company has extensive relevant experience.",
                claim_type="experience",
                is_factual=True,
                evidence_refs=[],
            )
        ]
    )
    section = await proposal_service.persist_section_and_claims(
        tender_id, "Relevant Experience", draft, [], {}
    )
    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
    assert claims[0].grounding_status == "unsupported"

    trace = await proposal_service.get_proposal_trace(claims[0].id)

    assert trace.evidence == []
    assert len(trace.currently_in_scope_evidence) == 1
    assert trace.currently_in_scope_evidence[0].evidence_link_id == real_links[0].id
    assert trace.section_is_data_gated is True


async def test_get_proposal_trace_currently_in_scope_evidence_excludes_already_cited():
    org_id, tender_id = await _make_org_and_tender("No Double Counting Test")
    _, entry_id = await _make_approved_requirement_with_evidence(
        tender_id=tender_id, org_id=org_id, category="experience"
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

    trace = await proposal_service.get_proposal_trace(claims[0].id)

    assert len(trace.evidence) == 1
    assert trace.evidence[0].evidence_link_id == real_links[0].id
    assert trace.currently_in_scope_evidence == [], (
        "the one real evidence link this tender has is already cited - it "
        "must not also appear as 'currently in scope but uncited'"
    )


async def test_get_proposal_trace_company_overview_is_not_data_gated():
    org_id, tender_id = await _make_org_and_tender("Company Overview Trace Test")
    draft = _section_draft(
        [
            ProposalClaimDraft(
                claim_text="General narrative statement about the company.",
                claim_type="capability",
                is_factual=False,
            )
        ]
    )
    section = await proposal_service.persist_section_and_claims(
        tender_id, "Company Overview", draft, [], {}
    )
    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)

    trace = await proposal_service.get_proposal_trace(claims[0].id)

    assert trace.currently_in_scope_evidence == []
    assert trace.section_is_data_gated is False


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


async def _draft_one_grounded_claim(
    tender_id: uuid.UUID, entry_id: uuid.UUID, real_evidence_link_id: uuid.UUID
) -> uuid.UUID:
    prepared = [
        proposal_service.PreparedFact(
            label="evidence_1",
            kind="evidence",
            excerpt="Delivered three telecom migrations.",
            evidence_link_id=real_evidence_link_id,
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
    assert claims[0].grounding_status == "grounded"
    return claims[0].id


async def test_reground_claim_is_a_no_op_when_evidence_is_unchanged():
    # The pin: kept even though the reconstruction risk this test was
    # originally written to guard against turned out smaller than planned
    # (enforce_claim_grounding reads only .is_factual/.claim_type off the
    # real persisted row - no ProposalClaimDraft reconstruction happens at
    # all). Still worth pinning byte-for-byte, because the one piece that
    # genuinely re-derives - _prepare_evidence_facts' verified/
    # validity_status filter, re-run here against current state rather than
    # the draft-time snapshot - could in principle behave differently on a
    # second pass even with nothing changed. It doesn't; this proves it.
    org_id, tender_id = await _make_org_and_tender("Reground No-Op Test")
    _, entry_id = await _make_approved_requirement_with_evidence(
        tender_id=tender_id, org_id=org_id, category="technical"
    )
    async with UnitOfWork() as uow:
        real_links = await uow.compliance.list_evidence_for_compliance_entry(entry_id)
    claim_id = await _draft_one_grounded_claim(tender_id, entry_id, real_links[0].id)

    async with UnitOfWork() as uow:
        result = await proposal_service.reground_claim(uow, claim_id)
        await uow.commit()

    assert result.status == "grounded"
    assert result.evidence_link_ids_considered == [real_links[0].id]
    async with UnitOfWork() as uow:
        reloaded = await uow.proposal.get_proposal_claim(claim_id)
    assert reloaded.grounding_status == "grounded", (
        "regrounding against unchanged evidence must be a true no-op"
    )


async def test_reground_claim_becomes_unsupported_when_its_evidence_goes_invalid():
    # The concrete case B14 exists for: a knowledge-selection deselection
    # (or a certification expiring) invalidates evidence a claim already
    # cites. No repository write path exists for mutating an evidence
    # link's own validity_status today (real invalidation happens
    # elsewhere - a reevaluation, an expiry check - not through
    # proposal_service), so this test reaches past the repository to
    # simulate that external change directly, the same way a real
    # deselection would leave the link behind it.
    org_id, tender_id = await _make_org_and_tender("Reground Invalidated Evidence Test")
    _, entry_id = await _make_approved_requirement_with_evidence(
        tender_id=tender_id, org_id=org_id, category="technical"
    )
    async with UnitOfWork() as uow:
        real_links = await uow.compliance.list_evidence_for_compliance_entry(entry_id)
    claim_id = await _draft_one_grounded_claim(tender_id, entry_id, real_links[0].id)

    async with get_session() as session:
        link = await session.get(EvidenceLink, real_links[0].id)
        link.validity_status = "expired"
        await session.commit()

    async with UnitOfWork() as uow:
        result = await proposal_service.reground_claim(uow, claim_id)
        await uow.commit()

    assert result.status == "unsupported"
    assert result.evidence_link_ids_considered == []
    async with UnitOfWork() as uow:
        reloaded = await uow.proposal.get_proposal_claim(claim_id)
    assert reloaded.grounding_status == "unsupported"


async def test_facts_from_pricing_sets_evidence_link_id_for_a_grounded_resolved_finding():
    """B16 Group 6: once commercial_service.resolve_pricing_finding
    materializes a real EvidenceLink (Option C), _facts_from_pricing must
    surface it via PreparedFact.evidence_link_id - not just offer the
    finding as unlinked narrative context the way B9 originally left it.
    Real fixture, real ingest -> resolve -> fact-building pipeline, no
    monkeypatching."""
    org_id, tender_id = await _make_org_and_tender("Pricing Facts Grounding Test")
    content = (_PRICING_FIXTURES_DIR / "pricing_schedule_1.xlsx").read_bytes()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing_schedule_1.xlsx",
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    client = FakeLLMClient()
    client.script(
        "commercial_explanation",
        FindingExplanation,
        [
            FindingExplanation(
                severity="major", explanation="A deterministic pricing check found this issue."
            )
        ]
        * 4,
    )
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, "fake-model", tender_id
    )
    await commercial_service.resolve_pricing_finding(findings[0].id, uuid.uuid4())

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_pricing(uow, tender_id, start_index=1)

    assert len(facts) == 1
    assert facts[0].evidence_link_id is not None, (
        "a resolved, grounded finding must be offered with a real evidence_link_id, "
        "not left unlinked the way B9 originally required"
    )


async def test_facts_from_pricing_excludes_a_resolved_findings_evidence_once_it_goes_invalid():
    """CodeRabbit-caught gap: _facts_from_pricing's own evidence_link_ids
    loop had no verified/validity_status re-check at all, unlike every
    other evidence path in this file (_prepare_evidence_facts' own
    explicit re-filter, with its own docstring explaining why - evidence
    can expire between materialization and a later drafting run). Same
    real ingest -> resolve pipeline as the grounded-finding test above,
    then the same "reach past the repository to simulate an external
    invalidation" technique test_reground_claim_becomes_unsupported_
    when_its_evidence_goes_invalid already uses (no repository write path
    mutates validity_status directly today - real invalidation happens
    elsewhere, a reevaluation or an expiry check).
    """
    org_id, tender_id = await _make_org_and_tender("Pricing Facts Invalidated Evidence Test")
    content = (_PRICING_FIXTURES_DIR / "pricing_schedule_1.xlsx").read_bytes()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing_schedule_1.xlsx",
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    client = FakeLLMClient()
    client.script(
        "commercial_explanation",
        FindingExplanation,
        [
            FindingExplanation(
                severity="major", explanation="A deterministic pricing check found this issue."
            )
        ]
        * 4,
    )
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, "fake-model", tender_id
    )
    await commercial_service.resolve_pricing_finding(findings[0].id, uuid.uuid4())

    async with UnitOfWork() as uow:
        facts_before = await proposal_service._facts_from_pricing(uow, tender_id, start_index=1)
    assert facts_before[0].evidence_link_id is not None

    async with get_session() as session:
        link = await session.get(EvidenceLink, facts_before[0].evidence_link_id)
        link.validity_status = "expired"
        await session.commit()

    async with UnitOfWork() as uow:
        facts_after = await proposal_service._facts_from_pricing(uow, tender_id, start_index=1)

    assert facts_after[0].evidence_link_id is None, (
        "an invalidated pricing evidence link must not still be offered as citable - "
        "the same re-check every other evidence path already applies"
    )


async def test_facts_from_pricing_leaves_evidence_link_id_unset_for_an_unresolved_finding():
    """Regression guard on the other half of the same function: an
    UNRESOLVED finding must still be filtered out entirely (unchanged B9
    behavior), not accidentally start appearing once grounding exists."""
    org_id, tender_id = await _make_org_and_tender("Pricing Facts Unresolved Test")
    content = (_PRICING_FIXTURES_DIR / "pricing_schedule_1.xlsx").read_bytes()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing_schedule_1.xlsx",
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    client = FakeLLMClient()
    client.script(
        "commercial_explanation",
        FindingExplanation,
        [
            FindingExplanation(
                severity="major", explanation="A deterministic pricing check found this issue."
            )
        ]
        * 4,
    )
    await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)

    async with UnitOfWork() as uow:
        facts = await proposal_service._facts_from_pricing(uow, tender_id, start_index=1)

    assert facts == []
