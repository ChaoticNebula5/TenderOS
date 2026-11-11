"""apps/worker/jobs/reanalysis.py against real Postgres - the orchestration
this job adds (blast-radius consumption -> compliance reevaluation ->
claim regrounding -> outbox events), not Neo4j's own correctness, which
test_graph_projection.py already proves separately. Most tests here
monkeypatch graph_service.project_tender/preview_impact to return a real
requirement_id/claim_id built directly, without needing a live Neo4j graph -
preview_impact's OWN query is already tested elsewhere, so what matters for
those tests is whether this job correctly drives downstream writes from its
output shape.

The graph-contradiction test below (B16) is the one exception, deliberately:
it does NOT monkeypatch project_tender/preview_impact, because the thing
under test - redteam_checks.check_graph_contradictions actually detecting a
real deselected-but-still-cited fact - only means something against a real
projected graph. Same real-Neo4j-plus-cleanup shape test_redteam_service.py
already established (project_tender for real, _clean_neo4j autouse fixture),
not a new pattern."""

import io
import uuid

import docx
import pytest
import pytest_asyncio
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
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_worker.jobs import reanalysis
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    # Autouse, matching test_redteam_service.py's own fixture verbatim -
    # every test in this file gets a clean graph regardless of whether it
    # personally touches Neo4j, so a test added later that does can't be
    # silently polluted by a projection an earlier test in this file left
    # behind (this file didn't need this before the graph-contradiction
    # test below; it does now, so it's here for everyone, not one test).
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


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
        claim_text="The company has delivered three telecom network migrations.",
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


async def _make_company_chunk_id(org_id: uuid.UUID, text: str) -> uuid.UUID:
    # Deliberately NOT _make_chunk_id above: that helper passes BOTH
    # tender_id and organization_id to create_document, and
    # document_service.create_document's own tender_id-XOR-organization_id
    # invariant means organization_id is dropped whenever tender_id is set
    # - confirmed directly (not assumed) by querying a chunk built that way
    # and finding documents.organization_id NULL. search_chunks_lexical_
    # for_org/search_chunks_vector_for_org both filter on Document.
    # organization_id, so a chunk from a tender-scoped document is
    # structurally invisible to search_company_knowledge regardless of
    # embedding quality - correct scoping (client-side RFP content must
    # never leak into org-wide retrieval), just the wrong shape for a
    # company-knowledge fixture. side="company" here is what
    # load_test_retrieval_latency.py (this session's own B16 load test)
    # already established for the identical need.
    doc = docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="company_knowledge.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="company_document",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    return chunks[0].id


async def _make_user() -> uuid.UUID:
    # Same shape as test_redteam_service.py's own helper of the same name -
    # duplicated rather than imported, matching this file's own established
    # "self-built fixture helpers, no cross-test-module imports" convention
    # (see the module docstring's own precedent, and _make_approved_
    # requirement_with_evidence's docstring above for the same rule stated
    # once already in this file).
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def test_reanalyze_knowledge_selection_change_surfaces_deselected_fact_still_used_finding(
    monkeypatch,
):
    """B16: the USED_IN structural gap (KNOWN_LIMITATIONS.md) means step (3)
    (compliance reevaluation) cannot make a deselection "reopen" a decision
    that has other-untouched evidence - compliance retrieval was never
    gated by selection, so re-evaluating finds the identical evidence and
    correctly persists the identical status. D6's own answer to this state
    is a contradiction FINDING, not an automatic reversal - this proves the
    new step (5) wiring for real, against a real projected graph, not a
    monkeypatched one: deselect a knowledge item that a compliance entry
    still cites, run the real job, assert deselected_fact_in_use actually
    surfaces as an open redteam_findings row - not just that the detector
    function works in isolation (test_redteam_service.py already proves
    that; what was unverified before this test is this job's own new call
    site, exactly the "capability exists, nothing exercises it" shape this
    project has hit before).
    """
    org_id, tender_id = await _make_org_and_tender("Reanalysis Deselected Fact")
    requirement_text = "The company must demonstrate three prior telecom migrations."
    # Two different chunks, deliberately: req_chunk_id is the requirement's
    # own tender-scoped source (irrelevant to retrieval, just satisfies the
    # column's NOT NULL); company_chunk_id is the org-scoped, embedded,
    # retrievable one search_company_knowledge below must actually find -
    # see _make_company_chunk_id's own comment for why these can't be the
    # same helper call.
    req_chunk_id = await _make_chunk_id(tender_id, org_id)
    company_chunk_id = await _make_company_chunk_id(org_id, requirement_text)
    actor_user_id = await _make_user()

    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Deselected Evidence Employee",
            content_fingerprint=str(uuid.uuid4()),
            title="Solutions Engineer",
        )
        # _compute_candidate_signals (compliance_service.py) only treats an
        # employee candidate as verified when profile_review_status ==
        # "verified" - create_employee's own default is not that (PRD 10.9's
        # own "review and approve employee profiles" workflow implies a real
        # review step, not verified-by-default). Without this,
        # enforce_evidence_sufficiency finds zero sufficient_evidence and
        # downgrades to evidence_unavailable regardless of what the
        # FakeLLMClient below says - confirmed by running this test before
        # adding this line and seeing exactly that.
        await uow.knowledge.update_employee(knowledge_item.id, profile_review_status="verified")
        # evaluate_compliance_for_requirement below re-runs
        # search_company_knowledge from scratch (real retrieval, not a
        # manually-inserted EvidenceLink alone) - this employee needs a
        # REAL embedded, org-scoped chunk to be found as a candidate, via
        # the same knowledge_item_chunks join table this session's own B16
        # fix built for exactly this (employee/résumé chunk association
        # has no scalar source_chunk_id column, unlike
        # certification/past_project).
        await uow.knowledge.associate_knowledge_item_chunks(knowledge_item.id, [company_chunk_id])
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-DESEL",
            requirement_text=requirement_text,
            normalized_text=requirement_text.lower(),
            category="experience",
            mandatory=True,
            source_chunk_id=req_chunk_id,
            content_fingerprint=str(uuid.uuid4()),
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status="fully_compliant"
        )
        evidence = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="employee",
            content_fingerprint=str(uuid.uuid4()),
            knowledge_item_id=knowledge_item.id,
            excerpt="Delivered three telecom network migrations for prior clients.",
            verified=True,
            validity_status="valid",
        )
        await uow.compliance.link_evidence(entry.id, evidence.id)
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        # Select then deselect - the real sequence a human takes
        # (full-flow.spec.ts's own step 14 selects before it ever
        # deselects), not a row inserted pre-deselected, so this exercises
        # the same UPDATE path a real deselection PATCH takes, not just an
        # INSERT with selected=False from the start.
        await uow.knowledge.upsert_selection(
            tender_id=tender_id,
            knowledge_item_id=knowledge_item.id,
            selected=True,
            actor_user_id=actor_user_id,
        )
        await uow.commit()
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(
            tender_id=tender_id,
            knowledge_item_id=knowledge_item.id,
            selected=False,
            actor_user_id=actor_user_id,
        )
        await uow.commit()

    # project_tender is step (1) of the job itself - not called separately
    # here, unlike this file's other tests, since this test deliberately
    # does NOT monkeypatch it (see the module docstring). The job's own
    # real projection is what MERGEs the USED_IN edge at selected=false
    # (D3 step 8) that find_deselected_fact_still_used_contradictions
    # depends on.
    fake_client = FakeLLMClient()
    fake_client.script(
        "compliance_proposal",
        ComplianceProposal,
        [
            ComplianceProposal(
                status="fully_compliant",
                evidence_refs=["evidence_1"],
                confidence=0.9,
                reasoning_summary="Prior telecom migration experience is documented.",
            )
        ],
    )
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda: fake_client)

    await reanalysis.reanalyze_knowledge_selection_change(
        str(tender_id), str(knowledge_item.id), state_version=7
    )

    async with UnitOfWork() as uow:
        refetched_entry = await uow.compliance.get_compliance_entry_by_requirement(requirement.id)
    assert refetched_entry.status == "fully_compliant", (
        "compliance retrieval was never gated by selection (compliance_service.py's own "
        "B7 comment) - re-evaluating after a deselection must find the SAME evidence and "
        "persist the SAME status, not invent a change that didn't happen"
    )
    assert refetched_entry.human_approved is True, (
        "an unchanged status must not clear human_approved - persist_compliance_decision's "
        "own documented behavior, and the whole reason step (3) alone cannot 'reopen' this"
    )

    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    matches = [f for f in findings if f.finding_category == "deselected_fact_in_use"]
    assert len(matches) == 1, (
        f"expected exactly one deselected_fact_in_use finding, found {len(matches)} "
        f"(all findings: {[(f.finding_category, f.target_type) for f in findings]})"
    )
    # The id is the compliance entry its evidence supports (plan item 35; this
    # line used to pin the wrong label, "evidence").
    assert matches[0].target_type == "compliance_entry"
    assert matches[0].target_id == refetched_entry.id
    assert matches[0].resolved is False
    assert matches[0].severity in ("critical", "major"), (
        "must be D7-coverage-graded via find_contradictions_for_redteam, not left at "
        "whatever a bare Cypher row would default to"
    )
