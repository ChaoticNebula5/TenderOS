"""Plan item 95: each writer of projected data moves the bid's state version
once when it changes something the graph shows, and not when it changes only
something the graph does not show; the version moves once per transaction;
and two selection changes in one transaction still get two versions.

The before/after shape matches the older per-service state_version tests.
Which writers exist, and that each calls a bump at all, is checked statically
by tests/unit/test_projection_bump_guard.py; these check the conditions.
"""

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.agents.knowledge.relationships import (
    RelationshipResolution,
    RelationshipVerdict,
)
from tenderos_backend.agents.knowledge.retrieval import KnowledgeSearchResult
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.requirements.verify import verify_and_persist
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.agents.schemas.talent import (
    CandidateScoring,
    ExtractedRole,
    RoleExtractionOutput,
    TalentStrength,
)
from tenderos_backend.agents.talent.extract_roles import RequirementRef
from tenderos_backend.db.models.knowledge import KnowledgeRelationship
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    commercial_service,
    compliance_service,
    knowledge_service,
    proposal_service,
    requirement_service,
    talent_service,
    tender_service,
)
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.compliance_service import (
    PreparedEvidence,
    _CandidateSignals,
)
from tenderos_backend.domain.services.proposal_service import PreparedFact
from tenderos_backend.domain.services.requirement_service import RequirementDraft
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio


async def _bid() -> dict[str, uuid.UUID]:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Bump Org",
    )
    tender = await create_tender(
        organization_id=registered.organization.id,
        name="Bump Bid",
        created_by=registered.user.id,
        submission_date=date(2027, 1, 15),
    )
    return {"org": registered.organization.id, "actor": registered.user.id, "tender": tender.id}


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        return tender.state_version


async def _chunk(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder shall have ten years of experience.",
            normalized_text="the bidder shall have ten years of experience.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return chunk.id


async def _requirement(
    tender_id: uuid.UUID, chunk_id: uuid.UUID, text: str = "Ten years."
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"EXP-{uuid.uuid4().hex[:4]}",
            requirement_text=text,
            normalized_text=text.lower(),
            category="experience",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


# ----------------------------------------------------------------------
# Once per transaction (addition 1)
# ----------------------------------------------------------------------


async def test_two_bumps_in_one_transaction_move_the_version_once():
    bid = await _bid()
    start = await _version(bid["tender"])
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.commit()
    assert await _version(bid["tender"]) == start + 1


async def test_bump_commit_bump_in_one_unit_of_work_moves_it_twice():
    bid = await _bid()
    start = await _version(bid["tender"])
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.commit()
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.commit()
    assert await _version(bid["tender"]) == start + 2


async def test_a_rollback_forgets_the_bump():
    bid = await _bid()
    start = await _version(bid["tender"])
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.rollback()
        await uow.tenders.bump_state_version(bid["tender"])
        await uow.commit()
    assert await _version(bid["tender"]) == start + 1


async def test_two_selection_changes_in_one_transaction_get_two_versions_and_two_events():
    """Addition 2: no path changes two selections in one transaction today,
    but if one did, the second KnowledgeSelectionChanged event must not share
    the first's key (knowledge_selection_changed:{tender}:{version}) and be
    dropped as a duplicate."""
    bid = await _bid()
    async with UnitOfWork() as uow:
        first, _ = await uow.knowledge.create_certification(
            organization_id=bid["org"], title="Cert A", content_fingerprint=uuid.uuid4().hex
        )
        second, _ = await uow.knowledge.create_certification(
            organization_id=bid["org"], title="Cert B", content_fingerprint=uuid.uuid4().hex
        )
        await uow.commit()
    start = await _version(bid["tender"])
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(bid["tender"])
        assert tender is not None
        for item_id in (first.id, second.id):
            await knowledge_service.record_knowledge_selection_in_uow(
                uow, tender, item_id, True, bid["actor"]
            )
        await uow.commit()
    assert await _version(bid["tender"]) == start + 2
    async with UnitOfWork() as uow:
        events = (
            (await uow.session.execute(select(OutboxEvent).order_by(OutboxEvent.created_at)))
            .scalars()
            .all()
        )
    keys = [e.idempotency_key for e in events if e.event_type == "KnowledgeSelectionChanged"]
    assert keys == [
        f"knowledge_selection_changed:{bid['tender']}:{start + 1}",
        f"knowledge_selection_changed:{bid['tender']}:{start + 2}",
    ]


# ----------------------------------------------------------------------
# The bid itself (U1)
# ----------------------------------------------------------------------


async def test_a_bid_edit_bumps_for_its_name_status_or_date_not_its_client():
    bid = await _bid()
    tender_id = bid["tender"]
    start = await _version(tender_id)
    await tender_service.update_tender(tender_id, client_name="New Client")
    assert await _version(tender_id) == start, "the client name is not projected"
    await tender_service.update_tender(tender_id, submission_date=date(2027, 1, 15))
    assert await _version(tender_id) == start, "the same date changes nothing"
    updated = await tender_service.update_tender(tender_id, submission_date=date(2027, 2, 15))
    assert await _version(tender_id) == start + 1
    assert updated is not None and updated.updated_at is not None, "still readable after close"
    await tender_service.update_tender(tender_id, name="Renamed Bid")
    await tender_service.update_tender(tender_id, status="submitted")
    assert await _version(tender_id) == start + 3


# ----------------------------------------------------------------------
# Requirements (U2 to U6, and reject)
# ----------------------------------------------------------------------


async def test_a_requirement_edit_bumps_only_for_category_or_mandatory():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    rid = await _requirement(bid["tender"], chunk)
    start = await _version(bid["tender"])

    async def patch(**kwargs) -> None:
        await requirement_service.patch_requirement(rid, bid["org"], bid["actor"], **kwargs)

    await patch()  # a bare confirm
    await patch(requirement_text="Twelve years.")
    await patch(category="experience", mandatory=True)  # the values it already has
    assert await _version(bid["tender"]) == start, "confirm, text and unchanged values"
    await patch(category="staffing")
    assert await _version(bid["tender"]) == start + 1
    await patch(mandatory=False)
    assert await _version(bid["tender"]) == start + 2
    await patch(action="reject")
    assert await _version(bid["tender"]) == start + 2, "rejected_at is not projected (item 97)"


async def test_merge_and_split_each_bump_once():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    a = await _requirement(bid["tender"], chunk, "Part A.")
    b = await _requirement(bid["tender"], chunk, "Part B.")
    start = await _version(bid["tender"])
    merged = await requirement_service.merge_requirements(
        [a, b], "Parts A and B.", "experience", True, bid["org"], bid["actor"]
    )
    assert await _version(bid["tender"]) == start + 1
    await requirement_service.split_requirement(
        merged.id,
        [{"requirement_text": "Part A again."}, {"requirement_text": "Part B again."}],
        bid["org"],
        bid["actor"],
    )
    assert await _version(bid["tender"]) == start + 2


async def test_extraction_bumps_once_for_the_batch_and_not_on_a_replay():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    async with UnitOfWork() as uow:
        document_id = (await uow.documents.get_chunk(chunk)).document_id  # type: ignore[union-attr]
    drafts = [
        RequirementDraft(
            requirement_text=text,
            category="technical",
            mandatory=True,
            document_id=document_id,
            source_chunk_id=chunk,
            confidence=0.9,
        )
        for text in ("Uptime of 99.9%.", "Encrypted at rest.", "Daily backups.")
    ]
    start = await _version(bid["tender"])
    await requirement_service.persist_extraction_results(bid["tender"], drafts)
    assert await _version(bid["tender"]) == start + 1
    await requirement_service.persist_extraction_results(bid["tender"], drafts)
    assert await _version(bid["tender"]) == start + 1, "a replay creates nothing"


async def test_verification_bumps_when_verified_changes():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    rid = await _requirement(bid["tender"], chunk)
    client = FakeLLMClient()
    supported = VerificationOutput(
        supported_by_source=True,
        mandatory_language_supported=True,
        verification_confidence=0.9,
        reasoning_summary="The source chunk states it.",
    )
    client.script("requirement_verification", VerificationOutput, [supported, supported])
    start = await _version(bid["tender"])
    await verify_and_persist(client, "fake", rid, bid["tender"])
    assert await _version(bid["tender"]) == start + 1
    await verify_and_persist(client, "fake", rid, bid["tender"])
    assert await _version(bid["tender"]) == start + 1, "verified again: no change"


async def test_a_submission_item_tick_never_bumps():
    """O1: submission items are not projected. Readiness no longer needs the
    bump (plan item 93); test_readiness_service checks its next generate
    drops the cleared blocker."""
    bid = await _bid()
    async with UnitOfWork() as uow:
        item = await uow.requirements.create_submission_item(
            bid["tender"], "Signed cover letter", True, uuid.uuid4().hex
        )
        await uow.commit()
    start = await _version(bid["tender"])
    await requirement_service.patch_submission_item(item.id, bid["org"], bid["actor"], "present")
    await requirement_service.patch_submission_item(item.id, bid["org"], bid["actor"], "missing")
    assert await _version(bid["tender"]) == start


# ----------------------------------------------------------------------
# Compliance (U7, O2, O4)
# ----------------------------------------------------------------------


async def _employee(org_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Pat Example",
            content_fingerprint=uuid.uuid4().hex,
            title="Pat Example",
            current_title="Engineer",
        )
        await uow.commit()
        return item.id


async def test_materialized_evidence_bumps_when_a_link_is_created_not_on_reuse():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    employee_id = await _employee(bid["org"])
    async with UnitOfWork() as uow:
        document_id = (await uow.documents.get_chunk(chunk)).document_id  # type: ignore[union-attr]
    prepared = [
        PreparedEvidence(
            label="evidence_1",
            result=KnowledgeSearchResult(
                knowledge_item_id=employee_id,
                item_type="employee",
                title="Pat Example",
                excerpt="Ten years.",
                document_id=document_id,
                chunk_id=chunk,
                page_num=None,
                heading_path=None,
                fusion_score=0.5,
                rerank_score=0.8,
            ),
            signals=_CandidateSignals(validity_status="valid", verified=True),
        )
    ]
    start = await _version(bid["tender"])
    await compliance_service.materialize_evidence(bid["tender"], prepared, ["evidence_1"])
    assert await _version(bid["tender"]) == start + 1
    await compliance_service.materialize_evidence(bid["tender"], prepared, ["evidence_1"])
    assert await _version(bid["tender"]) == start + 1, "the link is reused"


async def _entry(tender_id: uuid.UUID) -> uuid.UUID:
    chunk = await _chunk(tender_id)
    rid = await _requirement(tender_id, chunk)
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=rid, status="partially_compliant"
        )
        await uow.commit()
        return entry.id


async def test_a_re_evaluation_bumps_for_a_new_confidence_alone():
    """Check 1 at PR 10's review: the confidence is on the Compliance node,
    and a re-evaluation rewrites it. The column is REAL, so the same 0.8
    reads back as 0.800000011920929; the same confidence must not count."""
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    rid = await _requirement(bid["tender"], chunk)
    sufficiency = compliance_service.SufficiencyResult(
        status="partially_compliant", downgraded=False, reason="test"
    )

    async def evaluate(confidence: float) -> None:
        proposal = ComplianceProposal(
            status="partially_compliant", confidence=confidence, reasoning_summary="Some."
        )
        await compliance_service.persist_compliance_decision(rid, proposal, sufficiency, [])

    start = await _version(bid["tender"])
    await evaluate(0.8)
    assert await _version(bid["tender"]) == start + 1, "a new entry"
    await evaluate(0.8)
    assert await _version(bid["tender"]) == start + 1, "the same confidence, once stored"
    await evaluate(0.6)
    assert await _version(bid["tender"]) == start + 2, "same status and evidence, new confidence"


async def test_approving_twice_bumps_once():
    bid = await _bid()
    entry = await _entry(bid["tender"])
    start = await _version(bid["tender"])
    await compliance_service.approve_compliance_entry(entry, bid["actor"])
    await compliance_service.approve_compliance_entry(entry, bid["actor"])
    assert await _version(bid["tender"]) == start + 1


async def test_an_override_bumps_for_its_status_or_approval_not_its_reasoning():
    bid = await _bid()
    entry = await _entry(bid["tender"])
    start = await _version(bid["tender"])
    await compliance_service.override_compliance_entry(entry, bid["actor"], reasoning_summary="x")
    assert await _version(bid["tender"]) == start + 1, "it sets human_approved"
    await compliance_service.override_compliance_entry(entry, bid["actor"], reasoning_summary="y")
    assert await _version(bid["tender"]) == start + 1, "reasoning only, already approved"
    await compliance_service.override_compliance_entry(
        entry, bid["actor"], status="fully_compliant"
    )
    assert await _version(bid["tender"]) == start + 2


# ----------------------------------------------------------------------
# Talent (U8, U9, O3)
# ----------------------------------------------------------------------


async def test_role_extraction_and_match_evidence_bump_once_each():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    rid = await _requirement(bid["tender"], chunk)
    employee_id = await _employee(bid["org"])
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=bid["tender"], role_name="Lead", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(role.id, rid)
        employee = await uow.knowledge.get_employee(employee_id)
        await uow.commit()
    assert employee is not None
    client = FakeLLMClient()
    scoring = CandidateScoring(
        score=0.8,
        strengths=[TalentStrength(criterion="Experience", evidence_ref="evidence_1")],
        gaps=[],
    )
    client.script("talent_scoring", CandidateScoring, [scoring, scoring])
    start = await _version(bid["tender"])
    await talent_service.evaluate_role_match(client, "fake", role.id, employee)
    assert await _version(bid["tender"]) == start + 1, "a new evidence link"
    await talent_service.evaluate_role_match(client, "fake", role.id, employee)
    assert await _version(bid["tender"]) == start + 1, "a re-score reuses the link"


async def test_role_extraction_bumps_once_for_the_batch_and_not_on_a_replay():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    rid = await _requirement(bid["tender"], chunk)
    refs = [RequirementRef("req_1", rid, "Ten years.", chunk)]
    extraction = RoleExtractionOutput(
        roles=[
            ExtractedRole(role_name="Lead", source_requirement_refs=["req_1"]),
            ExtractedRole(role_name="Analyst", source_requirement_refs=["req_1"]),
        ]
    )
    start = await _version(bid["tender"])
    await talent_service.persist_extracted_roles(bid["tender"], extraction, refs)
    assert await _version(bid["tender"]) == start + 1
    await talent_service.persist_extracted_roles(bid["tender"], extraction, refs)
    assert await _version(bid["tender"]) == start + 1, "a replay creates nothing"


async def test_selecting_twice_bumps_once():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    await _requirement(bid["tender"], chunk)
    employee_id = await _employee(bid["org"])
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=bid["tender"], role_name="Lead", content_fingerprint=uuid.uuid4().hex
        )
        match = await uow.talent.create_role_match(role.id, employee_id)
        await uow.commit()
    start = await _version(bid["tender"])
    await talent_service.select_candidate(match.id, bid["actor"])
    await talent_service.select_candidate(match.id, bid["actor"])
    assert await _version(bid["tender"]) == start + 1


# ----------------------------------------------------------------------
# Proposal (U10 to U12)
# ----------------------------------------------------------------------


async def _section_and_claim(tender_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(
            tender_id=tender_id, section_name="Team"
        )
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Our lead has ten years.",
            claim_type="experience",
            is_factual=True,
            grounding_status="needs_review",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return section.id, claim.id


async def test_a_section_edit_bumps_when_its_status_changes_not_for_content():
    bid = await _bid()
    section_id, _ = await _section_and_claim(bid["tender"])
    start = await _version(bid["tender"])
    await proposal_service.update_section_content(section_id, "First edit.", bid["actor"])
    assert await _version(bid["tender"]) == start + 1, "draft to review"
    await proposal_service.update_section_content(section_id, "Second edit.", bid["actor"])
    assert await _version(bid["tender"]) == start + 1, "content only"


async def test_drafting_bumps_for_a_new_section_or_link_not_for_a_replay():
    bid = await _bid()
    chunk = await _chunk(bid["tender"])
    a = await _requirement(bid["tender"], chunk, "Part A.")
    b = await _requirement(bid["tender"], chunk, "Part B.")
    prepared = [
        PreparedFact(label="req_1", kind="requirement", excerpt="Part A.", requirement_id=a),
        PreparedFact(label="req_2", kind="requirement", excerpt="Part B.", requirement_id=b),
    ]

    def draft(*refs: str) -> ProposalSectionDraft:
        # A narrative claim: its grounding is "not_required" whatever it cites.
        claim = ProposalClaimDraft(
            claim_text="We understand the scope.",
            claim_type="experience",
            is_factual=False,
            requirement_refs=list(refs),
        )
        return ProposalSectionDraft(content="Draft text.", claims=[claim])

    async def persist(section_draft: ProposalSectionDraft) -> None:
        await proposal_service.persist_section_and_claims(
            bid["tender"], "Approach", section_draft, prepared, {}
        )

    start = await _version(bid["tender"])
    await persist(draft("req_1"))
    assert await _version(bid["tender"]) == start + 1, "a new section and claim"
    await persist(draft("req_1"))
    assert await _version(bid["tender"]) == start + 1, "a replay: same claim, same link"
    await persist(draft("req_1", "req_2"))
    assert await _version(bid["tender"]) == start + 2, "the same claim, one new link"

    # A section with no claims: only the section node is new.
    await proposal_service.persist_section_and_claims(
        bid["tender"], "Summary", ProposalSectionDraft(content="Short.", claims=[]), [], {}
    )
    assert await _version(bid["tender"]) == start + 3, "a new section, no claims"
    await proposal_service.persist_section_and_claims(
        bid["tender"], "Summary", ProposalSectionDraft(content="Rewritten.", claims=[]), [], {}
    )
    assert await _version(bid["tender"]) == start + 3, "a draft's content is not projected"


async def test_reground_bumps_when_the_grounding_changes():
    bid = await _bid()
    _, claim_id = await _section_and_claim(bid["tender"])
    start = await _version(bid["tender"])
    async with UnitOfWork() as uow:
        result = await proposal_service.reground_claim(uow, claim_id)
        await uow.commit()
    assert result.status != "needs_review", "the test's premise: the grounding changes"
    assert await _version(bid["tender"]) == start + 1
    async with UnitOfWork() as uow:
        await proposal_service.reground_claim(uow, claim_id)
        await uow.commit()
    assert await _version(bid["tender"]) == start + 1, "unchanged the second time"


# ----------------------------------------------------------------------
# Commercial (U13)
# ----------------------------------------------------------------------


async def test_resolving_a_cross_document_finding_bumps_once_for_its_two_links():
    bid = await _bid()
    async with UnitOfWork() as uow:
        refs = []
        for sheet in ("Schedule", "Summary"):
            document = await uow.documents.create(
                side="client",
                doc_type="rfp",
                filename=f"{sheet}.xlsx",
                storage_key=f"test/{uuid.uuid4()}",
                sha256=uuid.uuid4().hex,
                mime_type="application/octet-stream",
                file_size_bytes=10,
                tender_id=bid["tender"],
            )
            pricing_document = await uow.commercial.create_pricing_document(
                tender_id=bid["tender"], document_id=document.id, sheet_name=sheet
            )
            refs.append({"pricing_document_id": str(pricing_document.id)})
        finding = await uow.commercial.create_pricing_finding(
            tender_id=bid["tender"],
            finding_type="cross_doc_mismatch",
            note="Item 4 is 120.00 in the schedule and 150.00 in the summary.",
            content_fingerprint=uuid.uuid4().hex,
            item_a_ref=refs[0],
            item_b_ref=refs[1],
        )
        await uow.commit()
    start = await _version(bid["tender"])
    await commercial_service.resolve_pricing_finding(finding.id, bid["actor"])
    async with UnitOfWork() as uow:
        links = await uow.compliance.list_evidence_links_for_tender(bid["tender"])
    assert len(links) == 2, "the test's premise: two evidence links"
    assert await _version(bid["tender"]) == start + 1
    await commercial_service.resolve_pricing_finding(finding.id, bid["actor"])
    assert await _version(bid["tender"]) == start + 1, "a repeat resolve creates nothing"


# ----------------------------------------------------------------------
# Knowledge (U14 to U18): every bid whose graph includes the item
# ----------------------------------------------------------------------


async def _two_bids_and_an_employee() -> dict[str, uuid.UUID]:
    """One bid selecting the employee (a selection row reaches them), one bid
    that does not reach them."""
    bid = await _bid()
    other = await create_tender(organization_id=bid["org"], name="Other", created_by=bid["actor"])
    employee_id = await _employee(bid["org"])
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(bid["tender"], employee_id, True, bid["actor"])
        await uow.commit()
    return bid | {"other": other.id, "employee": employee_id}


async def _both(ids: dict[str, uuid.UUID]) -> list[int]:
    return [await _version(ids["tender"]), await _version(ids["other"])]


async def test_a_profile_edit_bumps_for_projected_fields_only():
    ids = await _two_bids_and_an_employee()
    start = await _both(ids)

    async def edit(**fields) -> None:
        await knowledge_service.patch_employee_profile(
            ids["employee"], ids["org"], ids["actor"], **fields
        )

    await edit(email="pat@example.com", professional_location="Pune", years_experience=9)
    await edit(current_title="Engineer")  # the title it already has
    assert await _both(ids) == start, "email, location, experience, an unchanged title"
    await edit(current_title="Principal Engineer")
    await edit(name="Pat Q. Example")
    await edit(availability_status="unavailable")
    assert await _both(ids) == [start[0] + 3, start[1]]


async def test_verifying_a_profile_bumps_when_its_review_or_eligibility_changes():
    ids = await _two_bids_and_an_employee()
    start = await _version(ids["tender"])

    async def verify(eligible: bool) -> None:
        await knowledge_service.verify_employee_profile(
            ids["employee"], ids["org"], ids["actor"], eligible
        )

    await verify(True)
    assert await _version(ids["tender"]) == start + 1
    await verify(True)
    assert await _version(ids["tender"]) == start + 1, "verified again: unchanged"
    await verify(False)
    assert await _version(ids["tender"]) == start + 2


async def test_an_item_edit_bumps_for_any_status_and_a_titled_items_title():
    ids = await _two_bids_and_an_employee()
    async with UnitOfWork() as uow:
        cert, _ = await uow.knowledge.create_certification(
            organization_id=ids["org"], title="ISO 27001", content_fingerprint=uuid.uuid4().hex
        )
        await uow.knowledge.upsert_selection(ids["tender"], cert.id, True, ids["actor"])
        await uow.commit()
    start = await _version(ids["tender"])

    async def edit(item_id: uuid.UUID, **fields) -> None:
        await knowledge_service.patch_knowledge_item(item_id, ids["org"], ids["actor"], **fields)

    await edit(ids["employee"], title="Pat Renamed")
    assert await _version(ids["tender"]) == start, "an employee node carries no item title"
    await edit(cert.id, title="ISO/IEC 27001")
    assert await _version(ids["tender"]) == start + 1, "a certificate node carries its title"
    await edit(cert.id, status="expired")
    assert await _version(ids["tender"]) == start + 2, "any status, not only archived (Q2)"
    await edit(cert.id, status="expired")
    assert await _version(ids["tender"]) == start + 2, "unchanged"


async def test_a_verified_relationship_bumps_only_bids_reaching_both_ends():
    ids = await _two_bids_and_an_employee()
    async with UnitOfWork() as uow:
        project, _ = await uow.knowledge.create_past_project(
            organization_id=ids["org"], title="Metro", content_fingerprint=uuid.uuid4().hex
        )
        # The other bid reaches the project only; the first reaches both ends.
        for tender_id in (ids["tender"], ids["other"]):
            await uow.knowledge.upsert_selection(tender_id, project.id, True, ids["actor"])
        await uow.commit()
    start = await _both(ids)
    candidate = SimpleNamespace(
        project=SimpleNamespace(knowledge_item_id=project.id), llm_confidence=0.8
    )

    async def import_with(verdict: RelationshipVerdict) -> None:
        await knowledge_service.import_worked_on_relationship(
            ids["org"],
            ids["employee"],
            candidate,  # type: ignore[arg-type]
            RelationshipResolution(verdict=verdict, reason="test"),
        )

    await import_with(RelationshipVerdict.NEEDS_REVIEW)
    assert await _both(ids) == start, "an unverified relationship is not projected"
    async with UnitOfWork() as uow:
        await uow.session.execute(
            delete(KnowledgeRelationship).where(KnowledgeRelationship.organization_id == ids["org"])
        )
        await uow.commit()
    await import_with(RelationshipVerdict.VERIFIED)
    assert await _both(ids) == [start[0] + 1, start[1]]


async def test_a_master_row_for_an_employee_on_file_bumps_when_their_title_changes():
    bid = await _bid()
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=bid["org"],
            import_type="employee_master_csv",
            source_document_id=None,
            created_by=bid["actor"],
        )
        await uow.commit()

    async def row(number: int, role: str) -> uuid.UUID:
        imported = await knowledge_service.import_employee_master_row(
            bid["org"],
            batch.id,
            EmployeeMasterRow(
                row_number=number,
                employee_code="EMP-95",
                name="Robin Row",
                role=role,
                status="active",
            ),
        )
        assert imported.target_knowledge_item_id is not None
        return imported.target_knowledge_item_id

    employee_id = await row(2, "Engineer")
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(bid["tender"], employee_id, True, bid["actor"])
        await uow.commit()
    start = await _version(bid["tender"])
    await row(3, "Engineer")
    assert await _version(bid["tender"]) == start, "the same title and status"
    await row(4, "Principal Engineer")
    assert await _version(bid["tender"]) == start + 1
