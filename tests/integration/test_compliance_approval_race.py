"""Plan item 59, PR 19: a compliance entry's re-evaluation, approval and
override, against each other.

A person's approval is an approval of the status the entry showed. Two ways
it could end up on a status the person never saw, both closed here:
- a re-evaluation read the entry unapproved, the approval committed, then the
  re-evaluation wrote its new status and kept the approval (its clear rule
  judged the stale read). All three writers now read the entry FOR UPDATE.
- a screen loaded before a re-evaluation: the approve request now carries the
  status the screen showed, compared under the entry's row lock; a different
  status is refused (409 COMPLIANCE_ENTRY_CHANGED) and nothing is written.

Lock order for every path that writes an entry and bumps its tender in one
transaction: the entry, then the tender.

Each race runs in one order, with no sleeps: one transaction is held open,
the other started, the test waits until Postgres shows it waiting on a lock,
then the first commits.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import compliance_service
from tenderos_backend.domain.services.compliance_service import SufficiencyResult

pytestmark = pytest.mark.asyncio

_ENTRY_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE '%compliance_entries%'"
)
# Any lock wait in this database. With the tender row held FOR UPDATE, a
# writer waits either at its bump (SELECT ... FOR UPDATE) or earlier, at an
# insert whose foreign key names the tender (its audit row takes a KEY SHARE
# lock on the tender): both are the tender.
_TENDER_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


async def _waiting_on(query, task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until `query` counts a lock wait, with `task` still
    running."""
    for _ in range(200):
        if task.done():
            break
        async with get_session() as session:
            if (await session.execute(query)).scalar_one() > 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


async def _entry(status: str = "fully_compliant") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A tender, a requirement on a chunk, and its compliance entry,
    unapproved. Returns (tender, requirement, entry)."""
    async with get_session() as session:
        org = Organization(name=f"Compliance Race {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Compliance Race", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
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
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate.",
            category="eligibility",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status=status
        )
        await uow.commit()
        return tender_id, requirement.id, entry.id


def _reevaluation(status: str) -> tuple[ComplianceProposal, SufficiencyResult]:
    proposal = ComplianceProposal(
        status=status, evidence_refs=[], confidence=0.9, reasoning_summary=f"Now {status}."
    )
    return proposal, SufficiencyResult(status=status, downgraded=False, reason="re-evaluated")


async def _row(entry_id: uuid.UUID) -> tuple[str, bool]:
    async with get_session() as session:
        entry = (
            await session.execute(select(ComplianceEntry).where(ComplianceEntry.id == entry_id))
        ).scalar_one()
        return entry.status, entry.human_approved


async def _actions(entry_id: uuid.UUID) -> list[str]:
    async with get_session() as session:
        rows = await session.execute(
            select(AuditEvent.action)
            .where(AuditEvent.target_type == "compliance_entry", AuditEvent.target_id == entry_id)
            .order_by(AuditEvent.created_at)
        )
        return list(rows.scalars().all())


async def _state_version(tender_id: uuid.UUID) -> int:
    async with get_session() as session:
        return (
            await session.execute(
                text("SELECT state_version FROM tenders WHERE id = :id"), {"id": tender_id}
            )
        ).scalar_one()


async def test_a_reevaluation_clears_an_approval_that_landed_while_it_ran():
    """The approval commits after the re-evaluation started and before it
    wrote: judged under the entry's lock, the re-evaluation sees the approval
    and clears it, since the status it writes is not the one approved.
    Judged on an unlocked read, it would keep the approval on a status the
    person never saw."""
    _tender_id, requirement_id, entry_id = await _entry("fully_compliant")
    proposal, sufficiency = _reevaluation("non_compliant")

    async with UnitOfWork() as holder:
        # The approval, held open: what approve_compliance_entry writes.
        await holder.session.execute(
            text("UPDATE compliance_entries SET human_approved = true WHERE id = :id"),
            {"id": entry_id},
        )
        reevaluation = asyncio.create_task(
            compliance_service.persist_compliance_decision(
                requirement_id, proposal, sufficiency, []
            )
        )
        await _waiting_on(_ENTRY_WAITING, reevaluation, "the re-evaluation never waited")
        await holder.commit()

    await asyncio.wait_for(reevaluation, timeout=10)
    assert await _row(entry_id) == ("non_compliant", False)
    assert await _actions(entry_id) == ["compliance_approval_cleared_on_reevaluation"]


async def test_an_approval_of_a_status_the_screen_no_longer_shows_is_refused():
    _tender_id, _requirement_id, entry_id = await _entry("fully_compliant")

    with pytest.raises(compliance_service.ComplianceEntryChangedError) as refused:
        await compliance_service.approve_compliance_entry(
            entry_id, uuid.uuid4(), shown_status="partially_compliant"
        )

    assert refused.value.current_status == "fully_compliant"
    assert await _row(entry_id) == ("fully_compliant", False)
    assert await _actions(entry_id) == []


async def test_an_approval_judges_the_shown_status_under_the_entry_lock():
    """A re-evaluation is writing a new status while the person approves the
    old one: the approval waits for it, then sees the new status and is
    refused. Compared before the lock, it would approve the old status's
    read and land on the new one."""
    tender_id, requirement_id, entry_id = await _entry("fully_compliant")
    version_before = await _state_version(tender_id)

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("UPDATE compliance_entries SET status = 'non_compliant' WHERE id = :id"),
            {"id": entry_id},
        )
        approval = asyncio.create_task(
            compliance_service.approve_compliance_entry(
                entry_id, uuid.uuid4(), shown_status="fully_compliant"
            )
        )
        await _waiting_on(_ENTRY_WAITING, approval, "the approval never waited for the entry")
        await holder.commit()

    with pytest.raises(compliance_service.ComplianceEntryChangedError):
        await asyncio.wait_for(approval, timeout=10)
    assert await _row(entry_id) == ("non_compliant", False)
    assert await _actions(entry_id) == []
    assert await _state_version(tender_id) == version_before


async def test_an_approval_of_the_shown_status_is_written():
    tender_id, _requirement_id, entry_id = await _entry("fully_compliant")
    version_before = await _state_version(tender_id)

    approved = await compliance_service.approve_compliance_entry(
        entry_id, uuid.uuid4(), shown_status="fully_compliant"
    )

    assert approved.human_approved is True
    assert await _row(entry_id) == ("fully_compliant", True)
    assert await _actions(entry_id) == ["compliance_approve"]
    assert await _state_version(tender_id) == version_before + 1


async def _entry_is_locked(entry_id: uuid.UUID) -> bool:
    """Whether another transaction holds the entry's row lock: a NOWAIT
    locked read from a third session fails at once if so."""
    async with get_session() as session:
        try:
            await session.execute(
                text("SELECT 1 FROM compliance_entries WHERE id = :id FOR UPDATE NOWAIT"),
                {"id": entry_id},
            )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                return True
            raise
        return False


@pytest.mark.parametrize("writer", ["approve", "override", "reevaluation"])
async def test_every_entry_writer_takes_the_entry_before_the_tender(writer):
    """Lock order: while another transaction holds the tender row, each
    writer waits on the tender (its bump) already holding the entry. A
    writer taking the tender first would hold nothing on the entry here,
    and two writers in opposite orders could deadlock."""
    tender_id, requirement_id, entry_id = await _entry("fully_compliant")

    async def write():
        if writer == "approve":
            return await compliance_service.approve_compliance_entry(
                entry_id, uuid.uuid4(), shown_status="fully_compliant"
            )
        if writer == "override":
            return await compliance_service.override_compliance_entry(
                entry_id, uuid.uuid4(), status="partially_compliant"
            )
        proposal, sufficiency = _reevaluation("non_compliant")
        return await compliance_service.persist_compliance_decision(
            requirement_id, proposal, sufficiency, []
        )

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM tenders WHERE id = :id FOR UPDATE"), {"id": tender_id}
        )
        task = asyncio.create_task(write())
        await _waiting_on(_TENDER_WAITING, task, f"{writer} never waited for the tender")
        assert await _entry_is_locked(entry_id), f"{writer} waits on the tender without the entry"
        await holder.commit()

    await asyncio.wait_for(task, timeout=10)


async def test_an_override_audits_the_status_it_replaced_as_it_was_when_written():
    """A re-evaluation is writing a new status while the person overrides:
    the override waits for it, and its audit row's "before" is the status it
    actually replaced. Read before the lock, it named the older status."""
    _tender_id, _requirement_id, entry_id = await _entry("fully_compliant")

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("UPDATE compliance_entries SET status = 'non_compliant' WHERE id = :id"),
            {"id": entry_id},
        )
        override = asyncio.create_task(
            compliance_service.override_compliance_entry(
                entry_id, uuid.uuid4(), status="partially_compliant"
            )
        )
        await _waiting_on(_ENTRY_WAITING, override, "the override never waited for the entry")
        await holder.commit()

    await asyncio.wait_for(override, timeout=10)
    assert await _row(entry_id) == ("partially_compliant", True)
    async with get_session() as session:
        before = (
            await session.execute(
                select(AuditEvent.before).where(
                    AuditEvent.target_id == entry_id, AuditEvent.action == "compliance_override"
                )
            )
        ).scalar_one()
    assert before["status"] == "non_compliant"
