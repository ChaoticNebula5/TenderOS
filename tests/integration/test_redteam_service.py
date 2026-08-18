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
    client.script(
        "redteam_critique", RedteamCritique, [RedteamCritique(findings=[])] * 5
    )
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


async def _requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID, mandatory: bool = True) -> uuid.UUID:
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


async def _select(tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool = True) -> None:
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


async def _role_with_gap_selected(tender_id: uuid.UUID, requirement_id: uuid.UUID, employee_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Solution Architect", content_fingerprint=uuid.uuid4().hex
        )
        await uow.talent.link_role_requirement_source(role.id, requirement_id)
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=employee_id
        )
        await uow.talent.update_role_match(
            match.id, human_selected=True, gaps=[{"criterion": "PMP certification", "status": "missing"}]
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
