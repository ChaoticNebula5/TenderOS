"""domain/services/compliance_service.py against real Postgres - evidence
materialization idempotency, compliance-entry persistence idempotency
(B7 stop-gate: a retry must not create a second copy of evidence or of
a compliance entry),
clarification generation, human override/approve auditing, and the
traceability walk."""

import io
import uuid
from datetime import date

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.knowledge.retrieval import KnowledgeSearchResult
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories.compliance import ComplianceRepository
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service, document_service
from tenderos_backend.domain.services.compliance_service import (
    MaterializedEvidence,
    SufficiencyResult,
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

    assert first.id == second.id, (
        "a retried compliance decision must update in place, not duplicate"
    )
    async with UnitOfWork() as uow:
        entries = await uow.compliance.list_compliance_entries_for_tender(tender_id)
    assert len(entries) == 1


async def test_persist_compliance_decision_does_not_bump_state_version_on_identical_retry():
    """CodeRabbit follow-up: persist_compliance_decision used to bump
    state_version unconditionally, even when re-run with the identical
    proposal/sufficiency/evidence (the same idempotent-retry shape
    test_persist_compliance_decision_is_idempotent_on_retry already proves
    doesn't duplicate the row). Every consumer of state_version (graph
    freshness, targeted-replan's no-progress check) reads a bump as "the
    graph is now stale" - bumping on a no-op retry falsely marks an
    otherwise-fresh graph stale for no actual change. A later call with a
    genuinely different status must still bump.
    """
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

    await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    async with UnitOfWork() as uow:
        after_first = (await uow.tenders.get(tender_id)).state_version

    await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    async with UnitOfWork() as uow:
        after_identical_retry = (await uow.tenders.get(tender_id)).state_version
    assert after_identical_retry == after_first, "an identical retry must not bump state_version"

    changed_proposal = ComplianceProposal(
        status="non_compliant",
        evidence_refs=[],
        confidence=0.4,
        reasoning_summary="Reassessed as non-compliant.",
    )
    changed_sufficiency = enforce_evidence_sufficiency(changed_proposal, [])
    await persist_compliance_decision(requirement_id, changed_proposal, changed_sufficiency, [])
    async with UnitOfWork() as uow:
        after_real_change = (await uow.tenders.get(tender_id)).state_version
    assert after_real_change == after_first + 1, "a genuine status change must still bump"


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

    updated = await approve_compliance_entry(entry.id, actor_id, shown_status=entry.status)

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


async def test_persist_compliance_decision_clears_stale_approval_on_material_status_change():
    """Regression test for the audit's finding #5: a human approves a
    fully_compliant result, an automated reevaluation later flips it to
    non_compliant (the underlying evidence changed), and human_approved
    used to survive unchanged - proposal_service.py's _facts_from_compliance
    trusts that flag unconditionally, so a stale approval would ground a
    claim inside a readiness report asserting everything checks out.
    """
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)

    proposal = ComplianceProposal(
        status="fully_compliant", evidence_refs=[], confidence=0.9, reasoning_summary="Compliant."
    )
    sufficiency = SufficiencyResult(status="fully_compliant", downgraded=False, reason="ok")
    entry = await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    actor_id = uuid.uuid4()
    approved = await approve_compliance_entry(entry.id, actor_id, shown_status=entry.status)
    assert approved.human_approved is True

    revised_proposal = ComplianceProposal(
        status="non_compliant",
        evidence_refs=[],
        confidence=0.9,
        reasoning_summary="No longer compliant - evidence expired.",
    )
    revised_sufficiency = SufficiencyResult(
        status="non_compliant", downgraded=False, reason="expired"
    )
    reevaluated = await persist_compliance_decision(
        requirement_id, revised_proposal, revised_sufficiency, []
    )

    assert reevaluated.status == "non_compliant"
    assert reevaluated.human_approved is False, (
        "a material status change on reevaluation must clear a stale human approval"
    )
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "compliance_entry",
                AuditEvent.target_id == entry.id,
                AuditEvent.action == "compliance_approval_cleared_on_reevaluation",
            )
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].actor_type == "system"


async def test_persist_compliance_decision_preserves_approval_on_identical_status():
    """The other half of the same fix: re-running analysis against
    unchanged underlying data (same status) is not a new decision - it must
    NOT clear an approval the human already gave for that same outcome.
    """
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)

    proposal = ComplianceProposal(
        status="fully_compliant", evidence_refs=[], confidence=0.9, reasoning_summary="Compliant."
    )
    sufficiency = SufficiencyResult(status="fully_compliant", downgraded=False, reason="ok")
    entry = await persist_compliance_decision(requirement_id, proposal, sufficiency, [])
    await approve_compliance_entry(entry.id, uuid.uuid4(), shown_status=entry.status)

    reevaluated = await persist_compliance_decision(requirement_id, proposal, sufficiency, [])

    assert reevaluated.status == "fully_compliant"
    assert reevaluated.human_approved is True, (
        "an identical semantic reevaluation must preserve an existing approval"
    )
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "compliance_entry",
                AuditEvent.target_id == entry.id,
                AuditEvent.action == "compliance_approval_cleared_on_reevaluation",
            )
        )
    assert result.scalars().all() == []


async def test_approve_compliance_entry_bumps_tender_state_version():
    # KNOWN_LIMITATIONS.md: "[B7/B8] tenders.state_version was never
    # bumped" - Part C's State-version invariant lists "compliance change"
    # explicitly, and this call was missing from B7 through this fix.
    # Asserts the bump itself, not just that approve_compliance_entry's
    # write succeeds - the whole point of the limitation entry is that a
    # missing bump produces no other visible symptom.
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    proposal = ComplianceProposal(
        status="evidence_unavailable", evidence_refs=[], confidence=0.3, reasoning_summary="x"
    )
    entry = await persist_compliance_decision(
        requirement_id, proposal, enforce_evidence_sufficiency(proposal, []), []
    )
    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await approve_compliance_entry(entry.id, uuid.uuid4(), shown_status=entry.status)

    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
    assert after_version == before_version + 1


async def test_override_compliance_entry_bumps_tender_state_version():
    # Same invariant as approve_compliance_entry's own test above, checked
    # against override_compliance_entry specifically - a separate function
    # body with its own bump_state_version call site, not proven merely
    # because approve's call site is proven. This is the exact shape of gap
    # the fix was meant to close: two call sites, two independent chances
    # to forget the bump.
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    proposal = ComplianceProposal(
        status="evidence_unavailable", evidence_refs=[], confidence=0.3, reasoning_summary="x"
    )
    entry = await persist_compliance_decision(
        requirement_id, proposal, enforce_evidence_sufficiency(proposal, []), []
    )
    async with UnitOfWork() as uow:
        before_version = (await uow.tenders.get(tender_id)).state_version

    await override_compliance_entry(entry.id, uuid.uuid4(), status="non_compliant")

    async with UnitOfWork() as uow:
        after_version = (await uow.tenders.get(tender_id)).state_version
    assert after_version == before_version + 1


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


# ----------------------------------------------------------------------
# Plan item 88, S2: a reused evidence link is judged on the signals this
# evaluation computed for its source, and its stored values are rewritten in
# persist_compliance_decision's transaction, with the state-version bump.
# The archive itself is written straight to the row here (not through
# knowledge_service), so no bump of its own hides whether the evaluation's
# write moves the version.
# ----------------------------------------------------------------------


async def _make_certification(org_id: uuid.UUID, chunk_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="ISO 27001",
            content_fingerprint=uuid.uuid4().hex,
            source_chunk_id=chunk_id,
        )
        await uow.commit()
        return knowledge_item.id


def _cert_result(knowledge_item_id: uuid.UUID, chunk_id: uuid.UUID) -> KnowledgeSearchResult:
    return KnowledgeSearchResult(
        knowledge_item_id=knowledge_item_id,
        item_type="certification",
        title="ISO 27001",
        excerpt="ISO 27001 certification covering data protection controls.",
        document_id=uuid.uuid4(),
        chunk_id=chunk_id,
        page_num=None,
        heading_path=None,
        fusion_score=0.5,
        rerank_score=0.9,
    )


async def _set_status_directly(knowledge_item_id: uuid.UUID, status: str) -> None:
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(knowledge_item_id, status=status)
        await uow.commit()


async def _stored(link_id: uuid.UUID) -> tuple[bool, str]:
    async with UnitOfWork() as uow:
        link = await uow.compliance.get_evidence_link(link_id)
        assert link is not None
        return link.verified, link.validity_status


async def _tender_version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        return tender.state_version


async def _evaluate(
    requirement_id: uuid.UUID,
    tender_id: uuid.UUID,
    results: list[KnowledgeSearchResult],
    status: str,
) -> tuple[ComplianceEntry, list[MaterializedEvidence]]:
    """The pipeline's own steps after retrieval and the model call, with the
    model's answer fixed: cite every candidate."""
    prepared = await prepare_evidence_candidates(results, today=date.today())
    labels = [p.label for p in prepared]
    proposal = ComplianceProposal(
        status=status, evidence_refs=labels, confidence=0.9, reasoning_summary="Cited."
    )
    materialized = await materialize_evidence(tender_id, prepared, labels)
    sufficiency = enforce_evidence_sufficiency(proposal, materialized)
    entry = await persist_compliance_decision(requirement_id, proposal, sufficiency, materialized)
    return entry, materialized


async def test_a_reused_link_is_judged_on_fresh_signals_after_an_archive_and_a_restore():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    cert_id = await _make_certification(org_id, chunk_id)
    results = [_cert_result(cert_id, chunk_id)]

    entry, materialized = await _evaluate(requirement_id, tender_id, results, "fully_compliant")
    link_id = materialized[0].id
    assert entry.status == "fully_compliant"
    assert await _stored(link_id) == (True, "valid")

    await _set_status_directly(cert_id, "archived")
    entry, materialized = await _evaluate(requirement_id, tender_id, results, "fully_compliant")
    assert [m.id for m in materialized] == [link_id], "the same row is reused"
    assert entry.status == "evidence_unavailable", (
        "the reused row still said valid; sufficiency must judge the archived source"
    )
    assert await _stored(link_id) == (False, "invalid")

    await _set_status_directly(cert_id, "active")
    entry, _ = await _evaluate(requirement_id, tender_id, results, "fully_compliant")
    assert entry.status == "fully_compliant", (
        "restored: a row stored invalid must not keep downgrading valid evidence"
    )
    assert await _stored(link_id) == (True, "valid")


async def test_a_refresh_alone_bumps_the_version_and_an_unchanged_retry_does_not():
    """partially_compliant with two cited links stays partially_compliant
    when one source is archived: same status, same evidence set. Only the
    refreshed link changed, and that alone must move the version.

    Starts from an approved entry and pins today's rule (user decision, PR
    8's review): a human approval is of the entry's status, so a
    refresh-only change keeps it and writes no approval-cleared audit row.
    The evidence under it is policed live instead (sufficiency, readiness
    step 2, the proposal's filter)."""
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    archived_cert = await _make_certification(org_id, chunk_id)
    kept_cert = await _make_certification(org_id, chunk_id)
    results = [_cert_result(archived_cert, chunk_id), _cert_result(kept_cert, chunk_id)]

    entry, _ = await _evaluate(requirement_id, tender_id, results, "partially_compliant")
    assert entry.status == "partially_compliant"
    await approve_compliance_entry(entry.id, uuid.uuid4(), shown_status=entry.status)
    settled = await _tender_version(tender_id)
    await _evaluate(requirement_id, tender_id, results, "partially_compliant")
    assert await _tender_version(tender_id) == settled, "nothing changed: no refresh, no bump"

    await _set_status_directly(archived_cert, "archived")
    assert await _tender_version(tender_id) == settled
    entry, materialized = await _evaluate(requirement_id, tender_id, results, "partially_compliant")
    assert entry.status == "partially_compliant"
    assert await _stored(materialized[0].id) == (False, "invalid")
    assert await _tender_version(tender_id) == settled + 1
    async with UnitOfWork() as uow:
        reread = await uow.compliance.get_compliance_entry(entry.id)
        cleared = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_id == entry.id,
                AuditEvent.action == "compliance_approval_cleared_on_reevaluation",
            )
        )
    assert reread is not None and reread.human_approved is True, (
        "an approval is of the status; a refresh under the same status keeps it"
    )
    assert cleared.scalars().all() == [], "no approval-cleared audit row for a refresh"


async def test_a_failure_after_materialize_leaves_the_link_and_the_version_as_they_were(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's Q1 condition: no path commits a refresh without the
    version move. materialize_evidence never writes a reused row, and the
    refresh in persist rolls back with the rest of its transaction."""
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    cert_id = await _make_certification(org_id, chunk_id)
    results = [_cert_result(cert_id, chunk_id)]
    _, materialized = await _evaluate(requirement_id, tender_id, results, "fully_compliant")
    link_id = materialized[0].id
    await _set_status_directly(cert_id, "archived")
    version = await _tender_version(tender_id)

    # A failure between materialize and persist (the sufficiency step):
    # materialize alone wrote nothing.
    prepared = await prepare_evidence_candidates(results, today=date.today())
    fresh = await materialize_evidence(tender_id, prepared, [prepared[0].label])
    assert (fresh[0].verified, fresh[0].validity_status) == (False, "invalid")
    assert await _stored(link_id) == (True, "valid")
    assert await _tender_version(tender_id) == version

    # A failure inside persist, after the refresh statement has run.
    async def _fail(self: ComplianceRepository, *args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated failure after the refresh")

    monkeypatch.setattr(ComplianceRepository, "link_evidence", _fail)
    proposal = ComplianceProposal(
        status="fully_compliant",
        evidence_refs=[prepared[0].label],
        confidence=0.9,
        reasoning_summary="Cited.",
    )
    with pytest.raises(RuntimeError):
        await persist_compliance_decision(
            requirement_id, proposal, enforce_evidence_sufficiency(proposal, fresh), fresh
        )
    assert await _stored(link_id) == (True, "valid")
    assert await _tender_version(tender_id) == version

    monkeypatch.undo()
    await persist_compliance_decision(
        requirement_id, proposal, enforce_evidence_sufficiency(proposal, fresh), fresh
    )
    assert await _stored(link_id) == (False, "invalid")
    assert await _tender_version(tender_id) == version + 1


async def test_a_model_failure_before_materialize_refreshes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The model call runs before materialize_evidence, so a failing call
    never reaches a refresh."""
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    cert_id = await _make_certification(org_id, chunk_id)
    results = [_cert_result(cert_id, chunk_id)]
    _, materialized = await _evaluate(requirement_id, tender_id, results, "fully_compliant")
    link_id = materialized[0].id
    await _set_status_directly(cert_id, "archived")
    version = await _tender_version(tender_id)

    async def _search(*args: object, **kwargs: object) -> list[KnowledgeSearchResult]:
        return results

    async def _model_fails(*args: object, **kwargs: object) -> ComplianceProposal:
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr(compliance_service, "search_company_knowledge", _search)
    monkeypatch.setattr(compliance_service, "propose_compliance", _model_fails)
    with pytest.raises(RuntimeError):
        await compliance_service.evaluate_compliance_for_requirement(
            FakeLLMClient(), "fake-model", requirement_id
        )
    assert await _stored(link_id) == (True, "valid")
    assert await _tender_version(tender_id) == version


async def test_the_compliance_trace_reads_an_archived_source_as_invalid():
    org_id, tender_id = await _make_tender()
    chunk_id = await _make_requirement_chunk(tender_id, org_id)
    requirement_id = await _make_requirement(tender_id, chunk_id)
    cert_id = await _make_certification(org_id, chunk_id)
    entry, _ = await _evaluate(
        requirement_id, tender_id, [_cert_result(cert_id, chunk_id)], "fully_compliant"
    )
    trace = await get_compliance_trace(entry.id)
    assert [e.validity_status for e in trace.evidence] == ["valid"]

    await _set_status_directly(cert_id, "archived")
    trace = await get_compliance_trace(entry.id)
    assert [e.validity_status for e in trace.evidence] == ["invalid"]
    assert await _stored(trace.evidence[0].evidence_link_id) == (True, "valid"), (
        "the trace reads the source live; the stored row is untouched until an evaluation"
    )

    await _set_status_directly(cert_id, "active")
    trace = await get_compliance_trace(entry.id)
    assert [e.validity_status for e in trace.evidence] == ["valid"]
