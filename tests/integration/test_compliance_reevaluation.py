"""Plan item 121 (user decisions 2026-11-26: Q1 a, Q2 a): a re-evaluation
compares the analysis's new answer with the analysis's own previous answer
(compliance_entries.automated_status), never with the status shown, which
after an override is the reviewer's.

- An unchanged answer keeps an override, its reasoning and its approval
  (T1, 2026-11-25: 85 overrides were undone by unchanged answers).
- A changed answer reopens an override: the approval is cleared and audited,
  the reviewer's status stays, and readiness blocks on it until it is
  approved again.
- An analysis entry behaves as before: an unchanged answer keeps an
  approval, a changed one takes the new status and clears it.

Real Postgres; persist_compliance_decision is given each answer directly,
after the sufficiency check, as the existing compliance tests do. Readiness
is evaluated on an unprojected graph; only this item's blocker is read.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service, readiness_service
from tenderos_backend.domain.services.compliance_service import (
    SufficiencyResult,
    approve_compliance_entry,
    override_compliance_entry,
    persist_compliance_decision,
)

pytestmark = pytest.mark.asyncio

CLEARED = "compliance_approval_cleared_on_reevaluation"
REVIEWER_REASONING = "Reviewer: the certificate is held on paper."

_ENTRY_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE '%compliance_entries%'"
)


async def _tender() -> tuple[uuid.UUID, uuid.UUID]:
    """A tender and one chunk every requirement here cites."""
    async with get_session() as session:
        org = Organization(name=f"Reevaluation {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Reevaluation", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.commit()
        tender_id = tender.id
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.docx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/octet-stream",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return tender_id, chunk.id


async def _requirement(tender_id: uuid.UUID, chunk_id: uuid.UUID, code: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=code,
            requirement_text=f"Requirement {code}.",
            normalized_text=f"requirement {code.lower()}.",
            category="eligibility",
            mandatory=True,
            source_chunk_id=chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


def _answer(
    status: str, reasoning: str = "The analysis's reasoning."
) -> tuple[ComplianceProposal, SufficiencyResult]:
    proposal = ComplianceProposal(
        status=status, evidence_refs=[], confidence=0.5, reasoning_summary=reasoning
    )
    return proposal, SufficiencyResult(status=status, downgraded=False, reason="test")


async def _evaluate(
    requirement_id: uuid.UUID, status: str, reasoning: str = "The analysis's reasoning."
):
    proposal, sufficiency = _answer(status, reasoning)
    return await persist_compliance_decision(requirement_id, proposal, sufficiency, [])


async def _row(entry_id: uuid.UUID) -> ComplianceEntry:
    async with get_session() as session:
        return (
            await session.execute(select(ComplianceEntry).where(ComplianceEntry.id == entry_id))
        ).scalar_one()


async def _events(entry_id: uuid.UUID, action: str = CLEARED) -> list[AuditEvent]:
    async with get_session() as session:
        rows = await session.execute(
            select(AuditEvent)
            .where(
                AuditEvent.target_type == "compliance_entry",
                AuditEvent.target_id == entry_id,
                AuditEvent.action == action,
            )
            .order_by(AuditEvent.created_at)
        )
        return list(rows.scalars().all())


async def _tender_events(tender_id: uuid.UUID, action: str = CLEARED) -> int:
    async with get_session() as session:
        rows = await session.execute(
            select(AuditEvent.id).where(
                AuditEvent.tender_id == tender_id, AuditEvent.action == action
            )
        )
        return len(rows.scalars().all())


async def _state_version(tender_id: uuid.UUID) -> int:
    async with get_session() as session:
        return (
            await session.execute(
                text("SELECT state_version FROM tenders WHERE id = :id"), {"id": tender_id}
            )
        ).scalar_one()


async def _reopened(tender_id: uuid.UUID) -> tuple[list[dict], int]:
    result = await readiness_service.evaluate_readiness(tender_id)
    blockers = [
        b
        for b in result.blockers
        if b["category"] == readiness_service.REOPENED_COMPLIANCE_DECISION
    ]
    return blockers, result.counts["compliance_decisions_reopened"]


async def _overridden(
    tender_id, chunk_id, code, analysis="evidence_unavailable", decided="fully_compliant"
):
    """An entry the analysis answered `analysis`, overridden to `decided`."""
    requirement_id = await _requirement(tender_id, chunk_id, code)
    entry = await _evaluate(requirement_id, analysis)
    await override_compliance_entry(
        entry.id, uuid.uuid4(), status=decided, reasoning_summary=REVIEWER_REASONING
    )
    return requirement_id, entry.id


async def test_t1_unchanged_answers_keep_85_overrides_and_4_approvals():
    """T1 (2026-11-25): 4 entries approved as the analysis answered, 85
    overridden from evidence_unavailable to fully_compliant; the revision
    pass then gave every requirement the same answer as before (89 of its 90
    calls were cache hits). Every override and approval must survive."""
    tender_id, chunk_id = await _tender()
    overridden = []
    for i in range(85):
        overridden.append(await _overridden(tender_id, chunk_id, f"REQ-{i:03d}"))
    approved = []
    for i in range(85, 89):
        requirement_id = await _requirement(tender_id, chunk_id, f"REQ-{i:03d}")
        entry = await _evaluate(requirement_id, "fully_compliant")
        await approve_compliance_entry(entry.id, uuid.uuid4(), shown_status="fully_compliant")
        approved.append((requirement_id, entry.id))

    for requirement_id, _entry_id in overridden:
        await _evaluate(requirement_id, "evidence_unavailable", "The same answer again.")
    for requirement_id, _entry_id in approved:
        await _evaluate(requirement_id, "fully_compliant", "The same answer again.")

    for _requirement_id, entry_id in overridden:
        row = await _row(entry_id)
        assert (row.status, row.human_approved, row.decided_by) == (
            "fully_compliant",
            True,
            "reviewer",
        )
        assert row.reasoning_summary == REVIEWER_REASONING
        assert (row.automated_status, row.automated_reasoning_summary) == (
            "evidence_unavailable",
            "The same answer again.",
        )
    for _requirement_id, entry_id in approved:
        row = await _row(entry_id)
        assert (row.status, row.human_approved, row.decided_by) == (
            "fully_compliant",
            True,
            "analysis",
        )
        assert row.reasoning_summary == "The same answer again."
    assert await _tender_events(tender_id) == 0
    assert await _reopened(tender_id) == ([], 0)


async def test_a_changed_answer_reopens_an_override_and_keeps_the_reviewers_status():
    tender_id, chunk_id = await _tender()
    requirement_id, entry_id = await _overridden(tender_id, chunk_id, "REQ-001")
    (override,) = await _events(entry_id, "compliance_override")
    assert (override.before["decided_by"], override.after["decided_by"]) == ("analysis", "reviewer")
    version = await _state_version(tender_id)

    # Same confidence and no evidence: only the cleared approval changes the
    # graph's Compliance node, since the status shown stays the reviewer's.
    await _evaluate(requirement_id, "partially_compliant", "Part of it is now covered.")

    row = await _row(entry_id)
    assert (row.status, row.reasoning_summary, row.decided_by) == (
        "fully_compliant",
        REVIEWER_REASONING,
        "reviewer",
    )
    assert row.human_approved is False
    assert (row.automated_status, row.automated_reasoning_summary) == (
        "partially_compliant",
        "Part of it is now covered.",
    )
    (event,) = await _events(entry_id)
    assert event.actor_type == "system"
    assert event.before == {
        "status": "fully_compliant",
        "human_approved": True,
        "decided_by": "reviewer",
        "automated_status": "evidence_unavailable",
    }
    assert event.after == {
        "status": "fully_compliant",
        "human_approved": False,
        "decided_by": "reviewer",
        "automated_status": "partially_compliant",
    }
    assert await _state_version(tender_id) > version, "a cleared approval is a graph change"


async def test_readiness_blocks_on_a_reopened_decision_only_until_it_is_approved_again():
    """The blocker's definition: a reviewer's decision that is not approved.
    An unapproved analysis entry and an approved reviewer entry are not
    reopened, and get no blocker."""
    tender_id, chunk_id = await _tender()
    reopened_requirement, reopened_entry = await _overridden(tender_id, chunk_id, "REQ-001")
    await _evaluate(reopened_requirement, "non_compliant")
    unapproved_requirement = await _requirement(tender_id, chunk_id, "REQ-002")
    await _evaluate(unapproved_requirement, "non_compliant")
    kept_requirement, _kept_entry = await _overridden(tender_id, chunk_id, "REQ-003")
    await _evaluate(kept_requirement, "evidence_unavailable")

    blockers, count = await _reopened(tender_id)
    assert count == 1
    assert [(b["target_type"], b["target_id"]) for b in blockers] == [
        ("compliance_entry", str(reopened_entry))
    ]
    assert blockers[0]["message"] == (
        "The compliance decision for requirement REQ-001 was reopened: the analysis now says "
        "non_compliant, the reviewer decided fully_compliant. Approve it again or change it on "
        "the Compliance page."
    )

    await approve_compliance_entry(reopened_entry, uuid.uuid4(), shown_status="fully_compliant")

    assert await _reopened(tender_id) == ([], 0)
    row = await _row(reopened_entry)
    assert (row.status, row.human_approved, row.decided_by) == ("fully_compliant", True, "reviewer")


async def test_an_analysis_entry_keeps_an_approval_on_an_unchanged_answer_and_loses_it_on_a_change():
    tender_id, chunk_id = await _tender()
    requirement_id = await _requirement(tender_id, chunk_id, "REQ-001")
    entry = await _evaluate(requirement_id, "fully_compliant")
    await approve_compliance_entry(entry.id, uuid.uuid4(), shown_status="fully_compliant")

    await _evaluate(requirement_id, "fully_compliant", "Unchanged.")
    row = await _row(entry.id)
    assert (row.status, row.human_approved, row.automated_status) == (
        "fully_compliant",
        True,
        "fully_compliant",
    )
    assert await _events(entry.id) == []

    await _evaluate(requirement_id, "non_compliant", "The certificate lapsed.")
    row = await _row(entry.id)
    assert (row.status, row.reasoning_summary, row.human_approved, row.decided_by) == (
        "non_compliant",
        "The certificate lapsed.",
        False,
        "analysis",
    )
    (event,) = await _events(entry.id)
    assert event.before == {
        "status": "fully_compliant",
        "human_approved": True,
        "decided_by": "analysis",
        "automated_status": "fully_compliant",
    }
    assert event.after == {
        "status": "non_compliant",
        "human_approved": False,
        "decided_by": "analysis",
        "automated_status": "non_compliant",
    }
    assert await _reopened(tender_id) == ([], 0)


async def test_a_reasoning_only_override_keeps_its_reasoning_through_a_reevaluation():
    tender_id, chunk_id = await _tender()
    requirement_id = await _requirement(tender_id, chunk_id, "REQ-001")
    entry = await _evaluate(requirement_id, "fully_compliant", "The analysis's first words.")
    await override_compliance_entry(entry.id, uuid.uuid4(), reasoning_summary=REVIEWER_REASONING)

    await _evaluate(requirement_id, "fully_compliant", "The analysis's new words.")

    row = await _row(entry.id)
    assert (row.status, row.reasoning_summary, row.human_approved) == (
        "fully_compliant",
        REVIEWER_REASONING,
        True,
    )
    assert row.automated_reasoning_summary == "The analysis's new words."


async def test_an_entry_already_reopened_writes_no_second_event():
    tender_id, chunk_id = await _tender()
    requirement_id, entry_id = await _overridden(tender_id, chunk_id, "REQ-001")
    await _evaluate(requirement_id, "partially_compliant")

    await _evaluate(requirement_id, "non_compliant")
    await _evaluate(requirement_id, "non_compliant")

    row = await _row(entry_id)
    assert (row.status, row.human_approved, row.automated_status) == (
        "fully_compliant",
        False,
        "non_compliant",
    )
    assert len(await _events(entry_id)) == 1
    blockers, count = await _reopened(tender_id)
    assert count == 1 and "the analysis now says non_compliant" in blockers[0]["message"]


async def test_an_override_landing_while_a_reevaluation_runs_is_kept_on_an_unchanged_answer():
    """The override commits after the re-evaluation started and before it
    wrote. Judged under the entry's lock, the re-evaluation sees a reviewer's
    decision and an unchanged analysis answer, and keeps both."""
    tender_id, chunk_id = await _tender()
    requirement_id = await _requirement(tender_id, chunk_id, "REQ-001")
    entry = await _evaluate(requirement_id, "evidence_unavailable")
    proposal, sufficiency = _answer("evidence_unavailable")

    async with UnitOfWork() as holder:
        # The override, held open: what override_compliance_entry writes.
        await holder.session.execute(
            text(
                "UPDATE compliance_entries SET status = 'fully_compliant', "
                "human_approved = true, decided_by = 'reviewer' WHERE id = :id"
            ),
            {"id": entry.id},
        )
        reevaluation = asyncio.create_task(
            compliance_service.persist_compliance_decision(
                requirement_id, proposal, sufficiency, []
            )
        )
        for _ in range(200):
            if reevaluation.done():
                break
            async with get_session() as session:
                if (await session.execute(_ENTRY_WAITING)).scalar_one() > 0:
                    break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the re-evaluation never waited")
        assert not reevaluation.done(), "the re-evaluation did not wait for the override"
        await holder.commit()

    await asyncio.wait_for(reevaluation, timeout=10)
    row = await _row(entry.id)
    assert (row.status, row.human_approved, row.decided_by) == ("fully_compliant", True, "reviewer")
    assert await _events(entry.id) == []
