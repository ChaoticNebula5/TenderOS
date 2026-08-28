"""apps/worker/jobs/reanalysis.py against real Postgres - the orchestration
this job adds (blast-radius consumption -> compliance reevaluation ->
claim regrounding -> outbox events), not Neo4j's own correctness, which
test_graph_projection.py already proves separately. graph_service.
project_tender/preview_impact are monkeypatched to return a real
requirement_id/claim_id this test built itself, without needing a live
Neo4j graph - preview_impact's OWN query is already tested elsewhere;
what's new here is whether this job correctly drives downstream writes
from its output shape."""

import io
import uuid

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.db.models.compliance import EvidenceLink
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, proposal_service
from tenderos_worker.jobs import reanalysis
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
    tender_id: uuid.UUID, org_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Same shape as test_proposal_service.py's own helper of the same name -
    duplicated rather than imported, since importing across test modules
    isn't this codebase's convention (every other test file builds its own
    copy of this fixture). Returns (requirement_id, entry_id, evidence_link_id).
    """
    chunk_id = await _make_chunk_id(tender_id, org_id)
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="The company must demonstrate three prior telecom migrations.",
            normalized_text="the company must demonstrate three prior telecom migrations",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=str(uuid.uuid4()),
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
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
        return requirement.id, entry.id, evidence.id


async def _draft_one_grounded_claim(
    tender_id: uuid.UUID, real_evidence_link_id: uuid.UUID
) -> uuid.UUID:
    prepared = [
        proposal_service.PreparedFact(
            label="evidence_1",
            kind="evidence",
            excerpt="Delivered three telecom migrations.",
            evidence_link_id=real_evidence_link_id,
        )
    ]
    draft_claim = ProposalClaimDraft(
        claim_text="The company completed three telecom migrations.",
        claim_type="experience",
        is_factual=True,
        evidence_refs=["evidence_1"],
    )
    section = await proposal_service.persist_section_and_claims(
        tender_id,
        "Relevant Experience",
        ProposalSectionDraft(content="Drafted section content.", claims=[draft_claim]),
        prepared,
        {},
    )
    async with UnitOfWork() as uow:
        claims = await uow.proposal.list_proposal_claims_for_section(section.id)
    assert claims[0].grounding_status == "grounded"
    return claims[0].id


async def test_reanalyze_knowledge_selection_change_reopens_compliance_and_regrounds_claim(
    monkeypatch,
):
    org_id, tender_id = await _make_org_and_tender("Reanalysis Job Test")
    requirement_id, entry_id, evidence_link_id = await _make_approved_requirement_with_evidence(
        tender_id, org_id
    )
    claim_id = await _draft_one_grounded_claim(tender_id, evidence_link_id)

    # The deselection's real effect on the world: the evidence this
    # requirement/claim relied on is no longer valid. The job's own job is
    # to notice this, not to invalidate it - that already happens via
    # whatever selection/evidence-lifecycle change triggered this job in
    # the first place (out of this test's own scope).
    async with get_session() as session:
        link = await session.get(EvidenceLink, evidence_link_id)
        link.validity_status = "expired"
        await session.commit()

    fake_rows = [
        {"labels": ["Requirement"], "affected": {"id": str(requirement_id)}},
        {"labels": ["ProposalClaim"], "affected": {"id": str(claim_id)}},
        # A row this job must ignore - Role is a real preview_impact label
        # (graph_service.preview_impact's own WHERE clause), not one of the
        # two this job acts on.
        {"labels": ["Role"], "affected": {"id": str(uuid.uuid4())}},
    ]

    async def _fake_project_tender(_tender_id: uuid.UUID) -> None:
        return None

    async def _fake_preview_impact(
        _tender_id: uuid.UUID, _knowledge_item_id: uuid.UUID
    ) -> list[dict]:
        return fake_rows

    monkeypatch.setattr(reanalysis.graph_service, "project_tender", _fake_project_tender)
    monkeypatch.setattr(reanalysis.graph_service, "preview_impact", _fake_preview_impact)

    fake_client = FakeLLMClient()
    fake_client.script(
        "compliance_proposal",
        ComplianceProposal,
        [
            ComplianceProposal(
                status="non_compliant",
                evidence_refs=[],
                confidence=0.9,
                reasoning_summary="No valid evidence remains for this requirement.",
            )
        ],
    )
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda: fake_client)

    await reanalysis.reanalyze_knowledge_selection_change(
        str(tender_id), str(uuid.uuid4()), state_version=42
    )

    async with UnitOfWork() as uow:
        entry = await uow.compliance.get_compliance_entry_by_requirement(requirement_id)
    assert entry.status == "non_compliant", "reevaluation must persist the new status"
    assert entry.human_approved is False, (
        "persist_compliance_decision's own stale-approval clearing is what makes this "
        "'compliance task reopens' - this job just has to call it, not reimplement it"
    )

    async with UnitOfWork() as uow:
        claim = await uow.proposal.get_proposal_claim(claim_id)
    assert claim.grounding_status == "unsupported", (
        "the claim's only evidence is now invalid, so reground_claim must find zero "
        "resolved_evidence_link_ids and enforce_claim_grounding must downgrade it"
    )

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type.in_(["ComplianceChanged", "ProposalChanged"])
            )
        )
    events = {e.event_type: e for e in result.scalars().all()}
    assert set(events) == {"ComplianceChanged", "ProposalChanged"}
    assert events["ComplianceChanged"].payload["requirement_id"] == str(requirement_id)
    assert events["ComplianceChanged"].payload["status"] == "non_compliant"
    assert events["ProposalChanged"].payload["claim_id"] == str(claim_id)
    assert events["ProposalChanged"].payload["grounding_status"] == "unsupported"
    assert events["ProposalChanged"].payload["evidence_link_ids_considered"] == []


async def test_affected_ids_splits_rows_by_label_and_ignores_unrelated_ones():
    requirement_id = uuid.uuid4()
    claim_id = uuid.uuid4()
    rows = [
        {"labels": ["Requirement"], "affected": {"id": str(requirement_id)}},
        {"labels": ["ProposalClaim"], "affected": {"id": str(claim_id)}},
        {"labels": ["Role"], "affected": {"id": str(uuid.uuid4())}},
        {"labels": ["Compliance"], "affected": {"id": str(uuid.uuid4())}},
        # A row with no resolvable id - preview_impact's own Cypher always
        # returns one for Requirement/ProposalClaim per graph_projection/
        # cypher/nodes.py, but this job must not assume that and crash on a
        # row shaped differently.
        {"labels": ["Requirement"], "affected": {}},
    ]

    requirement_ids, claim_ids = reanalysis._affected_ids(rows)

    assert requirement_ids == {requirement_id}
    assert claim_ids == {claim_id}
