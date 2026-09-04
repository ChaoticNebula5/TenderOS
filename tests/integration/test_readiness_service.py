"""domain/services/readiness_service.py against real Postgres AND real
Neo4j (blocker 7 reuses graph_service.check_freshness directly). Each of
the 8 blockers (the Build Guide's 7 required categories plus this block's
own red_team_review_incomplete addition, Q5) gets seeded individually and
confirmed both to appear alone and to clear once resolved - the B12 plan's
own checkpoint requirement. Every test that ISN'T specifically about
blocker 7 projects the graph after seeding, so staleness never
contaminates an otherwise-clean assertion.

Same self-built-fixture-helper convention as test_redteam_service.py.
"""

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.readiness import ReadinessReport
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    graph_service,
    readiness_service,
    redteam_checks,
    requirement_service,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


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


async def _chunk(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="req.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="req",
            normalized_text="req",
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


async def _compliance_entry(
    requirement_id: uuid.UUID, status: str = "fully_compliant", evidence_ids: list[uuid.UUID] | None = None
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        entry = await uow.compliance.create_compliance_entry(requirement_id=requirement_id, status=status)
        for evidence_id in evidence_ids or []:
            await uow.compliance.link_evidence(entry.id, evidence_id)
        await uow.commit()
        return entry.id


async def _employee(org_id: uuid.UUID, name: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id, name=name, content_fingerprint=uuid.uuid4().hex, title=name
        )
        await uow.commit()
        return knowledge_item.id


async def _evidence_link(tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, valid: bool = True) -> uuid.UUID:
    async with UnitOfWork() as uow:
        link = await uow.compliance.create_evidence_link(
            tender_id=tender_id,
            source_kind="certification",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=knowledge_item_id,
            verified=valid,
            validity_status="valid" if valid else "invalid",
        )
        await uow.commit()
        return link.id


async def _role_requirement(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=tender_id, role_name="Solution Architect", content_fingerprint=uuid.uuid4().hex
        )
        await uow.commit()
        return role.id


async def _role_match(role_requirement_id: uuid.UUID, employee_id: uuid.UUID, selected: bool) -> uuid.UUID:
    async with UnitOfWork() as uow:
        match = await uow.talent.create_role_match(
            role_requirement_id=role_requirement_id, employee_knowledge_item_id=employee_id
        )
        if selected:
            await uow.talent.update_role_match(match.id, human_selected=True)
        await uow.commit()
        return match.id


async def _pricing_finding(tender_id: uuid.UUID, severity: str = "critical", resolved: bool = False) -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note="Line total mismatch.",
            content_fingerprint=uuid.uuid4().hex,
            severity=severity,
        )
        if resolved:
            await uow.commercial.update_pricing_finding(finding.id, resolved=True)
        await uow.commit()
        return finding.id


async def _submission_item(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item = await uow.requirements.create_submission_item(
            tender_id=tender_id, name="Insurance proof", mandatory=True, content_fingerprint=uuid.uuid4().hex
        )
        await uow.commit()
        return item.id


async def _redteam_finding(tender_id: uuid.UUID, severity: str = "critical") -> uuid.UUID:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.create_finding(
            tender_id=tender_id,
            target_type="requirement",
            target_id=None,
            finding_category="missed_mandatory_requirement",
            severity=severity,
            finding_text="Test finding.",
            violated_condition="test",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return finding.id


async def _project(tender_id: uuid.UUID) -> None:
    await graph_service.project_tender(tender_id)


# ----------------------------------------------------------------------
# A fully clean tender is ready=True with zero blockers.
# ----------------------------------------------------------------------


async def test_clean_tender_is_ready():
    _org_id, tender_id = await _org_and_tender("Clean")
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert result.ready is True
    assert result.blockers == []


# B13/§8a: every test below only ever checks individual categories'
# presence/absence in result.blockers - none of them pin the COMPLETE
# counts key set schemas/readiness.py's ReadinessCounts is now typed
# against. All 11 keys are set unconditionally in one pass
# (readiness_service.py:184-319), so a clean tender's own report already
# carries every key (just at 0) - no extra seeding needed to pin the set
# itself, only to exercise individual values elsewhere in this file.
_EXPECTED_COUNTS_KEYS = {
    "requirements_total",
    "requirements_completed",
    "mandatory_requirements_unresolved",
    "compliance_decisions_awaiting_approval",
    "requirements_lacking_valid_evidence",
    "critical_major_redteam_findings",
    "missing_submission_artefacts",
    "commercial_findings_open",
    "resource_gaps",
    "proposal_claims_needing_review",
    "open_clarifications",
}


async def test_clean_tender_report_has_the_complete_counts_key_set():
    _org_id, tender_id = await _org_and_tender("Clean Counts")
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert set(result.counts.keys()) == _EXPECTED_COUNTS_KEYS, (
        "result.counts drifted from schemas/readiness.py's typed ReadinessCounts"
    )


# ----------------------------------------------------------------------
# Blocker 1: unresolved mandatory requirement.
# ----------------------------------------------------------------------


async def test_unresolved_mandatory_requirement_blocks_and_clears():
    _org_id, tender_id = await _org_and_tender("MandatoryReq")
    chunk_id = await _chunk(tender_id)
    requirement_id = await _requirement(tender_id, chunk_id, mandatory=True)
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    assert blocked.ready is False
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.UNRESOLVED_MANDATORY_REQUIREMENT in categories

    await _compliance_entry(requirement_id, status="fully_compliant")
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.UNRESOLVED_MANDATORY_REQUIREMENT not in categories


# ----------------------------------------------------------------------
# Blocker 2: compliant decision without valid evidence.
# ----------------------------------------------------------------------


async def test_compliant_decision_without_valid_evidence_blocks_and_clears():
    org_id, tender_id = await _org_and_tender("NoValidEvidence")
    chunk_id = await _chunk(tender_id)
    requirement_id = await _requirement(tender_id, chunk_id)
    entry_id = await _compliance_entry(requirement_id, status="fully_compliant")
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE in categories

    employee_id = await _employee(org_id, "Evidence Source")
    evidence_id = await _evidence_link(tender_id, employee_id, valid=True)
    async with UnitOfWork() as uow:
        await uow.compliance.link_evidence(entry_id, evidence_id)
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.COMPLIANT_DECISION_WITHOUT_VALID_EVIDENCE not in categories


# ----------------------------------------------------------------------
# Blocker 3: critical red-team finding.
# ----------------------------------------------------------------------


async def test_critical_redteam_finding_blocks_and_clears():
    # CodeRabbit review: this test previously projected BEFORE seeding the
    # finding, then never re-projected after bump_state_version below - so
    # the second evaluate_readiness call ran against a graph the bump had
    # already made stale, and STALE_GRAPH_PROJECTION would silently be
    # among `cleared.blockers` too. The assertion only checked for the
    # ABSENCE of CRITICAL_REDTEAM_FINDING, so a stale-graph blocker sneaking
    # in alongside it went completely undetected - every sibling blocker
    # test in this file already seeds-then-projects and re-projects after
    # its own bump; this one just hadn't matched that pattern.
    _org_id, tender_id = await _org_and_tender("CriticalFinding")
    finding_id = await _redteam_finding(tender_id, severity="critical")
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.CRITICAL_REDTEAM_FINDING in categories

    async with UnitOfWork() as uow:
        await uow.redteam.resolve_finding(finding_id, "fixed")
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.CRITICAL_REDTEAM_FINDING not in categories
    assert readiness_service.STALE_GRAPH_PROJECTION not in categories


# ----------------------------------------------------------------------
# Blocker 4: missing mandatory submission item.
# ----------------------------------------------------------------------


async def test_missing_mandatory_submission_item_blocks_and_clears():
    _org_id, tender_id = await _org_and_tender("MissingItem")
    item_id = await _submission_item(tender_id)
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.MISSING_MANDATORY_SUBMISSION_ITEM in categories

    async with UnitOfWork() as uow:
        await uow.requirements.update_submission_item(item_id, status="present")
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.MISSING_MANDATORY_SUBMISSION_ITEM not in categories


async def test_the_real_write_path_reaches_an_honest_unwaived_ready_true():
    """The assertion above only ever proved the BLOCKER computation clears
    when the repository is poked directly (uow.requirements.
    update_submission_item + a manual bump_state_version) - that's always
    worked. What has never been demonstrated, since B5, is a real write
    PATH: SubmissionRepository.update_submission_item had zero callers
    anywhere in either app until requirement_service.patch_submission_item
    (B16) gave it its first one. This test goes through that real service
    call - the same one PATCH /submission-items/{id} calls - not the bare
    repository method, and checks the two things that actually matter:
    (1) an otherwise-clean tender reaches a genuine, unwaived ready=True
    with zero remaining blockers, the assertion this project's own
    KNOWN_LIMITATIONS.md ("final readiness is unreachable through the UI
    by construction") has never been able to make; (2) redteam_checks.py's
    own missing_mandatory_submission_item finding - a second, independent
    consumer of the same 'missing' status this fix was not specifically
    built for - clears in the same pass, proving the write path fixes both
    readiness AND red-team, not just the one it was written for.
    """
    org_id, tender_id = await _org_and_tender("RealWritePath")
    item_id = await _submission_item(tender_id)
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    assert blocked.ready is False
    assert readiness_service.MISSING_MANDATORY_SUBMISSION_ITEM in {
        b["category"] for b in blocked.blockers
    }
    async with UnitOfWork() as uow:
        blocked_findings = await redteam_checks.check_submission_checklist(uow, tender_id)
    assert len(blocked_findings) == 1
    assert blocked_findings[0].finding_category == "missing_mandatory_submission_item"

    await requirement_service.patch_submission_item(
        item_id, org_id, uuid.uuid4(), status="present"
    )
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    assert cleared.ready is True
    assert cleared.blockers == []

    async with UnitOfWork() as uow:
        cleared_findings = await redteam_checks.check_submission_checklist(uow, tender_id)
    assert cleared_findings == []


# ----------------------------------------------------------------------
# Blocker 5: unapproved required commercial item.
# ----------------------------------------------------------------------


async def test_unapproved_required_commercial_item_blocks_and_clears():
    _org_id, tender_id = await _org_and_tender("CommercialItem")
    finding_id = await _pricing_finding(tender_id, severity="critical", resolved=False)
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.UNAPPROVED_REQUIRED_COMMERCIAL_ITEM in categories

    async with UnitOfWork() as uow:
        await uow.commercial.update_pricing_finding(finding_id, resolved=True)
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.UNAPPROVED_REQUIRED_COMMERCIAL_ITEM not in categories


async def test_minor_commercial_finding_is_not_a_blocker():
    _org_id, tender_id = await _org_and_tender("MinorCommercial")
    await _pricing_finding(tender_id, severity="minor", resolved=False)
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in result.blockers}
    assert readiness_service.UNAPPROVED_REQUIRED_COMMERCIAL_ITEM not in categories
    assert result.counts["commercial_findings_open"] == 1


# ----------------------------------------------------------------------
# Blocker 6: required role with no human-selected candidate.
# ----------------------------------------------------------------------


async def test_role_with_no_selected_candidate_blocks_and_clears():
    org_id, tender_id = await _org_and_tender("NoCandidate")
    role_id = await _role_requirement(tender_id)
    employee_id = await _employee(org_id, "Candidate")
    match_id = await _role_match(role_id, employee_id, selected=False)
    await _project(tender_id)

    blocked = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in blocked.blockers}
    assert readiness_service.ROLE_WITH_NO_SELECTED_CANDIDATE in categories

    async with UnitOfWork() as uow:
        await uow.talent.update_role_match(match_id, human_selected=True)
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    await _project(tender_id)

    cleared = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in cleared.blockers}
    assert readiness_service.ROLE_WITH_NO_SELECTED_CANDIDATE not in categories


# ----------------------------------------------------------------------
# Blocker 7: stale graph projection - the one test that deliberately never
# calls _project.
# ----------------------------------------------------------------------


async def test_stale_graph_projection_blocks_when_never_projected():
    _org_id, tender_id = await _org_and_tender("NeverProjected")

    result = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in result.blockers}
    assert readiness_service.STALE_GRAPH_PROJECTION in categories


async def test_stale_graph_projection_clears_once_projected():
    _org_id, tender_id = await _org_and_tender("BecomesFresh")
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id)
    categories = {b["category"] for b in result.blockers}
    assert readiness_service.STALE_GRAPH_PROJECTION not in categories


# ----------------------------------------------------------------------
# Blocker 8: red-team review degraded (this block's own addition, Q5).
# ----------------------------------------------------------------------


async def test_red_team_degraded_stage_blocks():
    _org_id, tender_id = await _org_and_tender("RedTeamDegraded")
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id, degraded_stages=["red_team:RuntimeError"])
    categories = {b["category"] for b in result.blockers}
    assert readiness_service.RED_TEAM_REVIEW_INCOMPLETE in categories


async def test_no_degraded_stages_does_not_block():
    _org_id, tender_id = await _org_and_tender("NoDegradation")
    await _project(tender_id)

    result = await readiness_service.evaluate_readiness(tender_id, degraded_stages=None)
    categories = {b["category"] for b in result.blockers}
    assert readiness_service.RED_TEAM_REVIEW_INCOMPLETE not in categories


# ----------------------------------------------------------------------
# Idempotent-by-version persistence.
# ----------------------------------------------------------------------


async def test_repeated_evaluation_at_the_same_version_returns_the_same_report():
    _org_id, tender_id = await _org_and_tender("IdempotentVersion")
    await _project(tender_id)

    first = await readiness_service.evaluate_readiness(tender_id)
    second = await readiness_service.evaluate_readiness(tender_id)

    assert first.report_id == second.report_id

    async with UnitOfWork() as uow:
        reports = await uow.session.execute(
            select(ReadinessReport).where(ReadinessReport.tender_id == tender_id)
        )
    assert len(list(reports.scalars().all())) == 1, (
        "must not insert a duplicate report for an unchanged version"
    )


# ----------------------------------------------------------------------
# CodeRabbit review: two races evaluate_readiness previously didn't handle -
# a concurrent insert at the same state_version (real uq_readiness_report_
# version violation, unhandled IntegrityError), and state_version itself
# moving between the read at the top of computation and the persist at the
# end.
# ----------------------------------------------------------------------


async def test_concurrent_generation_race_returns_the_winners_report(monkeypatch):
    """Simulates two evaluate_readiness calls racing for the same tender_id/
    state_version: both would see get_by_version return None (neither has
    committed yet) and both attempt create() - Postgres's own unique
    constraint lets exactly one through. Here, a "winner" row is inserted
    directly first (a real, already-committed row - not a mock), then
    ReadinessRepository.get_by_version is monkeypatched to report "nothing
    exists yet" on its first call, widening the real race window so this
    process's own create() genuinely collides with the real constraint.
    Everything after that first patched call - the actual IntegrityError,
    the rollback, the re-read - is real code, not simulated.
    """
    from tenderos_backend.db.repositories.readiness import ReadinessRepository

    _org_id, tender_id = await _org_and_tender("ConcurrentRace")
    await _project(tender_id)

    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        state_version = tender.state_version
        winner = await uow.readiness.create(
            tender_id, state_version, {"concurrent": True}, [], True
        )
        await uow.commit()

    original_get_by_version = ReadinessRepository.get_by_version
    call_count = {"n": 0}

    async def _flaky_get_by_version(self, tender_id_arg, state_version_arg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return await original_get_by_version(self, tender_id_arg, state_version_arg)

    monkeypatch.setattr(ReadinessRepository, "get_by_version", _flaky_get_by_version)

    result = await readiness_service.evaluate_readiness(tender_id)

    assert result.report_id == winner.id, (
        "losing the race must return the WINNER's already-committed report, "
        "not raise an unhandled IntegrityError"
    )

    async with UnitOfWork() as uow:
        reports = await uow.session.execute(
            select(ReadinessReport).where(ReadinessReport.tender_id == tender_id)
        )
    assert len(list(reports.scalars().all())) == 1, "must not leave two rows for the same version"


async def test_state_version_bump_during_computation_triggers_a_retry(monkeypatch):
    """The second race CodeRabbit flagged: state_version is read at the top
    of _evaluate_readiness_once but the report is only persisted at the
    end, after several separate reads. TenderRepository.get is patched to
    bump_state_version on its FIRST invocation only - simulating something
    else mutating the tender while THIS call is still mid-computation - so
    the persist-time re-check (a real read, not mocked) sees a version that
    no longer matches what was captured at the start, returns None, and
    evaluate_readiness's own retry wrapper recomputes fresh. Every
    subsequent call is the real, unpatched method, so the retry converges
    normally.
    """
    from tenderos_backend.db.repositories.tenders import TenderRepository

    _org_id, tender_id = await _org_and_tender("VersionRace")
    await _project(tender_id)

    original_get = TenderRepository.get
    call_count = {"n": 0}

    async def _bump_once_then_normal(self, tender_id_arg):
        tender = await original_get(self, tender_id_arg)
        call_count["n"] += 1
        if call_count["n"] == 1 and tender is not None:
            async with UnitOfWork() as bump_uow:
                await bump_uow.tenders.bump_state_version(tender_id_arg)
                await bump_uow.commit()
        return tender

    monkeypatch.setattr(TenderRepository, "get", _bump_once_then_normal)

    result = await readiness_service.evaluate_readiness(tender_id)

    async with UnitOfWork() as uow:
        current_version = (await uow.tenders.get(tender_id)).state_version
    assert result.tender_state_version == current_version, (
        "the persisted report must reflect the tender's state_version AFTER "
        "the mid-computation bump, proving the retry actually recomputed "
        "against fresh state rather than persisting under a stale version"
    )
    assert call_count["n"] >= 2, "the bump must have actually been triggered mid-computation"


# ----------------------------------------------------------------------
# CodeRabbit review: blockers 1 and 2 used to issue one query PER
# requirement/compliance entry - correctness-equivalent to the batched
# version either way, invisible on this file's other small fixtures, but
# real O(N) cost on a tender with hundreds of real requirements, paid
# twice per check (the manual generate route and the orchestration node
# both call evaluate_readiness). This seeds enough requirements that the
# difference is actually measurable, not just "the same blockers come out
# the other end" (which would pass against the old, unbatched code too).
# ----------------------------------------------------------------------


async def test_batched_compliance_and_evidence_queries_scale_with_requests_not_requirements():
    from sqlalchemy import event

    from tenderos_backend.db.session import engine

    org_id, tender_id = await _org_and_tender("BatchedQueries")
    chunk_id = await _chunk(tender_id)

    requirement_count = 60
    for i in range(requirement_count):
        requirement_id = await _requirement(tender_id, chunk_id, mandatory=True)
        employee_id = await _employee(org_id, f"Evidence Source {i}")
        evidence_id = await _evidence_link(tender_id, employee_id, valid=True)
        await _compliance_entry(requirement_id, status="fully_compliant", evidence_ids=[evidence_id])
    await _project(tender_id)

    query_count = {"n": 0}

    def _count(conn, cursor, statement, parameters, context, executemany):
        query_count["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        result = await readiness_service.evaluate_readiness(tender_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)

    assert result.ready is True
    # If blockers 1 and 2 still issued one query per requirement/entry,
    # this alone would be >= 2 * requirement_count (120) queries just for
    # those two checks, on top of every other blocker's own queries. A
    # small, roughly constant count regardless of requirement_count is the
    # actual proof the batching fix works.
    assert query_count["n"] < 30, (
        f"expected a small, roughly constant query count regardless of "
        f"requirement count, got {query_count['n']} for {requirement_count} requirements - "
        "blockers 1 and/or 2 may have regressed back to one query per item"
    )
