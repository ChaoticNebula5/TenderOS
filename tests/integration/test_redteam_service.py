"""domain/services/redteam_service.py + redteam_checks.py against real
Postgres AND real Neo4j (2 of the 7 required seeded-defect categories are
graph contradictions consumed via B10's own D6 queries). All 7 of B11's
required seeded-defect categories, transcribed from the Build Guide
verbatim, plus the integration-level half of "Red Team cannot silently
edit approved content" (Q3) - the unit-level grep proof lives in
tests/unit/test_redteam_no_silent_edit.py.

Same fixture-helper shape as tests/integration/test_graph_projection.py
(direct repository calls, no S3/parsing pipeline) - self-built here rather
than imported, matching every other integration test file's own
"self-built fixture helpers" convention in this codebase.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories.tenders import TenderRepository
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, redteam_service
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


def _fake_client_with_empty_critique() -> FakeLLMClient:
    # Every automated test in this codebase scripts FakeLLMClient rather
    # than calling real Gemini (see agents/redteam/critique.py's own
    # docstring). Scripted to return an empty findings list: these tests
    # are about the 4 deterministic/graph checks and the persistence
    # layer, not the adversarial step's own judgment, which has no seeded
    # ground truth to score against here. run_red_team calls the
    # critique agent exactly once per run_red_team invocation, so one
    # scripted response per call in this file's tests is enough.
    client = FakeLLMClient()
    client.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])] * 5)
    return client


async def _make_user() -> uuid.UUID:
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def _org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name=f"{name} Tender", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _chunk(tender_id: uuid.UUID, label: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename=f"{label}.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text=label,
            normalized_text=label.lower(),
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return chunk.id


async def _requirement(
    tender_id: uuid.UUID, chunk_id: uuid.UUID, mandatory: bool = True
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category="experience",
            mandatory=mandatory,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


async def _employee(org_id: uuid.UUID, name: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id, name=name, content_fingerprint=uuid.uuid4().hex, title=name
        )
        await uow.commit()
        return knowledge_item.id


async def _certification(org_id: uuid.UUID, holder_id: uuid.UUID, valid_until: date) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Sample Certification",
            content_fingerprint=uuid.uuid4().hex,
            holder_employee_item_id=holder_id,
            valid_until=valid_until,
        )
        await uow.commit()
        return knowledge_item.id


async def _select(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool = True
) -> None:
    actor_user_id = await _make_user()
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(
            tender_id=tender_id,
            knowledge_item_id=knowledge_item_id,
            selected=selected,
            actor_user_id=actor_user_id,
        )
        await uow.commit()


async def _evidence_link(tender_id: uuid.UUID, knowledge_item_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        link = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="certification",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=knowledge_item_id,
            verified=True,
            validity_status="valid",
        )
        await uow.commit()
        return link.id


async def _compliance_entry(requirement_id: uuid.UUID, evidence_ids: list[uuid.UUID]) -> uuid.UUID:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement_id, status="fully_compliant"
        )
        for evidence_id in evidence_ids:
            await uow.compliance.link_evidence(entry.id, evidence_id)
        await uow.compliance.update_compliance_entry(entry.id, human_approved=True)
        await uow.commit()
        return entry.id


async def _role_with_gap_selected(
    tender_id: uuid.UUID, requirement_id: uuid.UUID, employee_id: uuid.UUID
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id,
            role_name="Solution Architect",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.talent.link_role_requirement_source(role.id, requirement_id)
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_id
        )
        await uow.talent.update_role_match(
            match.id,
            human_selected=True,
            gaps=[{"criterion": "PMP certification", "status": "missing"}],
        )
        await uow.commit()
        return match.id


async def _proposal_claim(
    tender_id: uuid.UUID,
    employee_id: uuid.UUID | None = None,
    grounding_status: str = "grounded",
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(
            tender_id=tender_id, section_name=f"Team {uuid.uuid4().hex[:8]}"
        )
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Sample claim text.",
            claim_type="team",
            is_factual=True,
            grounding_status=grounding_status,
            content_fingerprint=uuid.uuid4().hex,
        )
        if employee_id is not None:
            await uow.proposal.link_claim_employee(claim.id, employee_id)
        await uow.commit()
        return claim.id


async def _pricing_finding(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note="Line total does not equal unit cost times quantity.",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return finding.id


async def _missing_submission_item(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item = await uow.requirements.create_submission_item(
            tender_id=tender_id,
            name="Proof of professional indemnity insurance",
            mandatory=True,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return item.id


async def _seed_all_seven(name: str) -> uuid.UUID:
    """One tender carrying all 7 required seeded-defect categories at
    once, matching the plan's Q4 answer: 3 arise from data the same shape
    real fixtures already produce (pricing, missing submission item,
    graph-detected expired cert/removed employee), the other 4 are
    constructed directly because they're pipeline-behavior states a
    document generator can't plant.
    """
    org_id, tender_id = await _org_and_tender(name)
    chunk_id = await _chunk(tender_id, "req")

    # 1. missed_mandatory_requirement - a mandatory requirement with zero
    # compliance_entries. No compliance_entry created for it at all.
    await _requirement(tender_id, chunk_id, mandatory=True)

    # 2. unsupported_claim - a factual claim never marked grounded.
    await _proposal_claim(tender_id, grounding_status="unsupported")

    # 3. expired_certification (graph contradiction) - a compliant
    # decision backed by a cert that expired before the tender's own
    # submission_date.
    cert_holder_id = await _employee(org_id, "Cert Holder")
    expired_cert_id = await _certification(org_id, cert_holder_id, valid_until=date(2020, 1, 1))
    cert_requirement_id = await _requirement(tender_id, chunk_id)
    cert_evidence_id = await _evidence_link(tender_id, expired_cert_id)
    await _compliance_entry(cert_requirement_id, [cert_evidence_id])

    # 4. removed_employee_committed (graph contradiction) - an employee
    # deselected for this tender but still named in a claim.
    removed_employee_id = await _employee(org_id, "Removed Employee")
    await _select(tender_id, removed_employee_id, selected=True)
    await _select(tender_id, removed_employee_id, selected=False)
    await _proposal_claim(tender_id, employee_id=removed_employee_id)

    # 5. pricing_mismatch - wraps an existing, already-computed
    # pricing_finding (B8B's own deterministic engine, not recomputed here).
    await _pricing_finding(tender_id)

    # 6. missing_mandatory_submission_item ("missing annexure" is one
    # instance of this general category, per the plan's Q4 answer).
    await _missing_submission_item(tender_id)

    # 7. role_qualification_gap - the human-selected candidate has a
    # non-empty gaps list.
    role_requirement_id = await _requirement(tender_id, chunk_id)
    gap_employee_id = await _employee(org_id, "Gap Candidate")
    await _role_with_gap_selected(tender_id, role_requirement_id, gap_employee_id)

    await graph_service.project_tender(tender_id)
    return tender_id


async def _run(tender_id: uuid.UUID) -> list[str]:
    client = _fake_client_with_empty_critique()
    return await redteam_service.run_red_team(client, "fake-model", tender_id)


# ----------------------------------------------------------------------
# All 7 required seeded-defect categories.
# ----------------------------------------------------------------------


async def test_all_seven_seeded_defect_categories_are_detected():
    tender_id = await _seed_all_seven("SevenDefects")
    await _run(tender_id)

    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    categories = {f.finding_category for f in findings}

    required = {
        "missed_mandatory_requirement",
        "unsupported_claim",
        "expired_certification",
        "removed_employee_committed",
        "pricing_mismatch",
        "missing_mandatory_submission_item",
        "role_qualification_gap",
    }
    missing = required - categories
    assert not missing, f"seeded categories never detected: {missing}"


async def test_critical_categories_are_marked_critical():
    tender_id = await _seed_all_seven("CriticalSeverity")
    await _run(tender_id)

    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    by_category = {f.finding_category: f.severity for f in findings}

    assert by_category["missed_mandatory_requirement"] == "critical"
    assert by_category["unsupported_claim"] == "critical"
    assert by_category["expired_certification"] == "critical"
    assert by_category["removed_employee_committed"] == "critical"


# ----------------------------------------------------------------------
# Q3: structural no-silent-edit, integration half.
# ----------------------------------------------------------------------


async def test_red_team_never_mutates_the_content_it_examines():
    tender_id = await _seed_all_seven("NoSilentEdit")

    async with UnitOfWork() as uow:
        before_entries = sorted(
            (e.id, e.status, e.human_approved)
            for e in await uow.compliance.list_compliance_entries_for_tender(tender_id)
        )
        sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
        before_claims = []
        for section in sections:
            for claim in await uow.proposal.list_proposal_claims_for_section(section.id):
                before_claims.append((claim.id, claim.claim_text, claim.grounding_status))
        before_claims.sort()

    open_ids = await _run(tender_id)
    assert open_ids, "expected real findings for this to be a meaningful proof"

    async with UnitOfWork() as uow:
        after_entries = sorted(
            (e.id, e.status, e.human_approved)
            for e in await uow.compliance.list_compliance_entries_for_tender(tender_id)
        )
        sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
        after_claims = []
        for section in sections:
            for claim in await uow.proposal.list_proposal_claims_for_section(section.id):
                after_claims.append((claim.id, claim.claim_text, claim.grounding_status))
        after_claims.sort()

    assert before_entries == after_entries
    assert before_claims == after_claims


# ----------------------------------------------------------------------
# MERGE-not-delete: a human's resolution survives an identical re-detection.
# ----------------------------------------------------------------------


async def test_corrected_finding_is_auto_retired_from_the_open_set():
    """Regression test for the audit's finding #19: run_red_team upserted
    current findings and then returned ALL historically unresolved ones,
    with nothing marking a finding whose underlying defect was actually
    fixed as no longer open. That both stalled route_after_red_team's own
    "no progress" check (comparing open_critical_finding_ids across passes)
    and would put a phantom blocker in a future readiness report. Here: a
    missed_mandatory_requirement finding is real on pass 1; the missing
    compliance_entry is created before pass 2, so the same fingerprint
    should no longer be (re-)detected - the row must leave the open set
    (auto-resolved, with a system-authored note) without being deleted.
    """
    org_id, tender_id = await _org_and_tender("AutoRetire")
    chunk_id = await _chunk(tender_id, "req")
    requirement_id = await _requirement(tender_id, chunk_id, mandatory=True)
    # check_graph_contradictions (one of the 5 checks run_red_team always
    # runs) requires a fresh projection - _seed_all_seven's own fixture
    # does this too. Fixing the defect below never bumps state_version
    # (the test helpers call the repository directly, not the state-
    # version-bumping service functions), so this one projection stays
    # fresh for both passes.
    await graph_service.project_tender(tender_id)

    first_open_ids = await _run(tender_id)

    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    target = next(f for f in findings if f.finding_category == "missed_mandatory_requirement")
    assert str(target.id) in first_open_ids or target.severity != "critical", (
        "the seeded missed_mandatory_requirement finding must be real and open after pass 1"
    )
    assert target.resolved is False

    # Fix the underlying defect: the requirement now has a compliance
    # decision, so check_missing_mandatory_requirements will no longer
    # draft this fingerprint on the next pass.
    await _compliance_entry(requirement_id, [])

    second_open_ids = await _run(tender_id)

    async with UnitOfWork() as uow:
        refetched = await uow.redteam.get(target.id)
    assert refetched is not None, "the row itself must survive, not be deleted"
    assert refetched.resolved is True, "a corrected finding must be retired from the open set"
    assert (
        refetched.resolution_note is not None
        and "auto-retired" in refetched.resolution_note.lower()
    )
    assert str(target.id) not in second_open_ids


async def test_auto_retired_finding_reopens_when_the_defect_reappears():
    """CodeRabbit review of the #19 fix: auto-retiring a finding
    (resolved=True) with nothing distinguishing it from a human resolution
    meant persist_finding_draft's own upsert returned the still-resolved row
    untouched even when the SAME fingerprint was detected again - a
    currently-real, possibly-critical finding sitting silently in the
    resolved pile, worse than the original bug and plausible in exactly
    the revision loop this mechanism exists to drive (a corrective task
    fails to actually fix the defect, or a later change reintroduces it).
    resolution_source (not a resolution_note string match, which would
    drift between the two write sites) is what lets reopening distinguish
    "the system retired this" from "a human resolved this."
    """
    org_id, tender_id = await _org_and_tender("ReopenOnReappear")
    pricing_finding_id = await _pricing_finding(tender_id)
    await graph_service.project_tender(tender_id)

    await _run(tender_id)
    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    target = next(f for f in findings if f.finding_category == "pricing_mismatch")
    assert target.resolved is False

    # Fix it: pass 2 no longer detects this fingerprint, auto-retiring it.
    async with UnitOfWork() as uow:
        await uow.commercial.update_pricing_finding(pricing_finding_id, resolved=True)
        await uow.commit()
    await _run(tender_id)
    async with UnitOfWork() as uow:
        retired = await uow.redteam.get(target.id)
    assert retired is not None
    assert retired.resolved is True
    assert retired.resolution_source == "system"

    # The defect comes back: pass 3 detects the same fingerprint again.
    async with UnitOfWork() as uow:
        await uow.commercial.update_pricing_finding(pricing_finding_id, resolved=False)
        await uow.commit()
    await _run(tender_id)

    async with UnitOfWork() as uow:
        reopened = await uow.redteam.get(target.id)
        open_major = await uow.redteam.list_open_for_tender(tender_id, ("critical", "major"))
    assert reopened is not None
    assert reopened.resolved is False, "a reappearing defect must reopen an auto-retired finding"
    assert reopened.resolution_source is None
    assert reopened.resolution_note is None
    assert reopened.id in {f.id for f in open_major}, "the reopened finding must be in the open set"


async def test_reopened_finding_refreshes_severity_from_the_current_detection():
    """CodeRabbit follow-up to the reopen fix: finding_fingerprint
    deliberately excludes severity/finding_text (so a re-detection with
    different wording still upserts onto the same row), but that means a
    defect that comes back at a DIFFERENT severity than when it was first
    seen - major, now critical - would otherwise stay stored at the OLD
    severity, since reopen_finding only cleared resolved/resolution_note/
    resolution_source and left severity untouched. That directly
    undermines B11's critical-recall stop-gate and revision routing, both
    of which read the stored column: a critical regression sitting in the
    DB labeled 'major' would never enter open_critical_finding_ids, never
    route through route_after_red_team's revision loop, and never appear
    in a future readiness report's critical-blocker list.
    """
    org_id, tender_id = await _org_and_tender("SeverityRefreshOnReopen")
    pricing_finding_id = await _pricing_finding(tender_id)  # severity="major" by default
    await graph_service.project_tender(tender_id)

    critical_ids_pass1 = await _run(tender_id)
    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    target = next(f for f in findings if f.finding_category == "pricing_mismatch")
    assert target.severity == "major"
    assert str(target.id) not in critical_ids_pass1, (
        "a major finding must not be in the critical set"
    )

    # Fix it (auto-retire), then bring it back at a DIFFERENT, worse severity.
    async with UnitOfWork() as uow:
        await uow.commercial.update_pricing_finding(pricing_finding_id, resolved=True)
        await uow.commit()
    await _run(tender_id)

    async with UnitOfWork() as uow:
        await uow.commercial.update_pricing_finding(
            pricing_finding_id, resolved=False, severity="critical"
        )
        await uow.commit()
    critical_ids_pass3 = await _run(tender_id)

    async with UnitOfWork() as uow:
        reopened = await uow.redteam.get(target.id)
    assert reopened is not None
    assert reopened.resolved is False
    assert reopened.severity == "critical", (
        f"reopening must refresh severity from the current draft, got {reopened.severity!r}"
    )
    assert str(reopened.id) in critical_ids_pass3, (
        "a finding reopened at critical severity must enter the revision set"
    )


async def test_human_resolved_finding_does_not_reopen_when_redetected():
    """The case the fix above must not break: a human's resolution is a
    deliberate judgment call (e.g. "accepted, known discrepancy") - it must
    survive the SAME fingerprint being detected again, exactly like
    test_resolved_finding_survives_an_unchanged_redetection already proves
    for an unchanged redetection. This is the reappearing-defect case
    instead: the underlying condition genuinely comes back, and a human
    resolution must still not be silently reopened by the system.
    """
    org_id, tender_id = await _org_and_tender("HumanResolutionSurvives")
    await _pricing_finding(tender_id)
    await graph_service.project_tender(tender_id)

    await _run(tender_id)
    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
        target = next(f for f in findings if f.finding_category == "pricing_mismatch")
        await uow.redteam.resolve_finding(target.id, "accepted, known pricing discrepancy")
        await uow.commit()

    # The underlying pricing_finding was never actually fixed - the next
    # pass would detect the same fingerprint again regardless.
    await _run(tender_id)

    async with UnitOfWork() as uow:
        refetched = await uow.redteam.get(target.id)
    assert refetched is not None
    assert refetched.resolved is True, "a human resolution must not be reopened by redetection"
    assert refetched.resolution_source == "human"
    assert refetched.resolution_note == "accepted, known pricing discrepancy"


async def test_resolved_finding_survives_an_unchanged_redetection():
    tender_id = await _seed_all_seven("ResolvedSurvives")
    await _run(tender_id)

    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
        target = next(f for f in findings if f.finding_category == "pricing_mismatch")
        await uow.redteam.resolve_finding(target.id, "accepted, known pricing discrepancy")
        await uow.commit()

    # Re-run red_team without changing the underlying pricing_finding - the
    # same fingerprint should upsert onto the SAME row, not create a
    # second one that clobbers the resolution.
    await _run(tender_id)

    async with UnitOfWork() as uow:
        refetched = await uow.redteam.get(target.id)
    assert refetched is not None
    assert refetched.resolved is True
    assert refetched.resolution_note == "accepted, known pricing discrepancy"


async def test_resolving_a_finding_whose_tender_is_missing_fails_before_any_write(monkeypatch):
    """B17 Phase 0b runtime behaviour change (mypy narrowing in
    redteam_service.resolve_finding): the tender used to be read inline,
    after the resolve update, as `(await uow.tenders.get(...)).organization_id`
    - a missing tender surfaced as an anonymous AttributeError. It is now
    looked up first and raises FindingTenderMissingError before any write.
    A real missing tender is impossible to construct (cascading FK), so the
    lookup is made to return None for this one tender.
    """
    _org_id, tender_id = await _org_and_tender("TenderMissingOnResolve")
    await _pricing_finding(tender_id)
    await graph_service.project_tender(tender_id)
    await _run(tender_id)
    async with UnitOfWork() as uow:
        findings = await uow.redteam.list_for_tender(tender_id)
    target = next(f for f in findings if f.finding_category == "pricing_mismatch")

    original_get = TenderRepository.get

    async def get_missing_for_this_tender(self, requested_id):
        if requested_id == tender_id:
            return None
        return await original_get(self, requested_id)

    monkeypatch.setattr(TenderRepository, "get", get_missing_for_this_tender)
    with pytest.raises(redteam_service.FindingTenderMissingError, match=str(tender_id)):
        await redteam_service.resolve_finding(target.id, uuid.uuid4(), "should not apply")
    monkeypatch.undo()

    async with UnitOfWork() as uow:
        refetched = await uow.redteam.get(target.id)
    assert refetched is not None
    assert refetched.resolved is False, "nothing may be written when the tender is missing"
