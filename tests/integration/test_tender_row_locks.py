"""Plan item 59, PR 19: the lock a writer takes on its tender's row.

Two calls lock the row: the bump (TenderRepository.increment_state_version)
and the pricing resolve's first lock (TenderRepository.get_for_update). Both
take FOR NO KEY UPDATE, which still makes a second holder wait. FOR UPDATE,
which both took before, also conflicts with the KEY SHARE lock every insert
of a row that references the tender takes for its foreign key. So an upload
or the commercial node's new finding waited for any resolve on the bid, and
two transactions that each inserted such a row and then bumped (two
approvals, each with its audit row) each waited on the other: a deadlock.

Steps run in one order, with no sleeps: a transaction is paused just before
its bump by awaiting an event, the other is started, and the test polls
pg_stat_activity until it waits on a lock or finishes.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select, text
from tenderos_backend.db.models.commercial import PricingFinding
from tenderos_backend.db.models.compliance import ComplianceEntry
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import commercial_service, compliance_service, tender_service

pytestmark = pytest.mark.asyncio

_SOMEONE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)
_WAITING_QUERY = text(
    "SELECT query FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


@dataclass
class _Bid:
    tender_id: uuid.UUID
    sheets: list[uuid.UUID]
    findings: list[uuid.UUID]
    entries: list[uuid.UUID]


async def _bid() -> _Bid:
    """A tender with two pricing sheets, a finding on each, and two
    unapproved compliance entries (fully_compliant)."""
    async with get_session() as session:
        org = Organization(name=f"Tender Locks {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Tender Locks", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        tender_id = tender.id
    sheets: list[uuid.UUID] = []
    findings: list[uuid.UUID] = []
    entries: list[uuid.UUID] = []
    async with UnitOfWork() as uow:
        for n in (1, 2):
            document = await uow.documents.create(
                side="client",
                doc_type="rfp",
                filename=f"pricing-{n}.xlsx",
                storage_key=f"test/{uuid.uuid4()}",
                sha256=uuid.uuid4().hex,
                mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                file_size_bytes=10,
                tender_id=tender_id,
            )
            sheet = await uow.commercial.create_pricing_document(
                tender_id=tender_id, document_id=document.id, sheet_name="Schedule"
            )
            finding = await uow.commercial.create_pricing_finding(
                tender_id=tender_id,
                finding_type="wrong_total",
                note=f"Item {n} total is wrong.",
                content_fingerprint=uuid.uuid4().hex,
                pricing_document_id=sheet.id,
            )
            chunk = await uow.documents.create_chunk(
                document_id=document.id,
                chunk_index=0,
                chunk_text=f"Requirement {n}.",
                normalized_text=f"requirement {n}.",
                content_hash=uuid.uuid4().hex,
            )
            requirement = await uow.requirements.create(
                tender_id=tender_id,
                display_code=f"REQ-00{n}",
                requirement_text=f"Requirement {n}.",
                normalized_text=f"requirement {n}.",
                category="eligibility",
                mandatory=True,
                source_chunk_id=chunk.id,
                content_fingerprint=uuid.uuid4().hex,
            )
            entry = await uow.compliance.create_compliance_entry(
                requirement_id=requirement.id, status="fully_compliant"
            )
            sheets.append(sheet.id)
            findings.append(finding.id)
            entries.append(entry.id)
        await uow.commit()
    return _Bid(tender_id, sheets, findings, entries)


async def _someone_waits() -> bool:
    async with get_session() as session:
        return (await session.execute(_SOMEONE_WAITING)).scalar_one() > 0


async def _waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until a session waits on a lock, with `task` still
    running."""
    for _ in range(200):
        if task.done():
            break
        if await _someone_waits():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


async def _finished_without_waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s for `task` to finish, failing as soon as any session
    of this database waits on a lock."""
    for _ in range(200):
        if task.done():
            await task
            return
        if await _someone_waits():
            raise AssertionError(f"{what}: it waited on a lock")
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: it never finished")


async def _finished_or_waiting(task: asyncio.Task, what: str) -> None:
    for _ in range(200):
        if task.done() or await _someone_waits():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: it neither finished nor waited")


def _pause_before_bump(
    uow: UnitOfWork, monkeypatch: pytest.MonkeyPatch
) -> tuple[asyncio.Event, asyncio.Event]:
    """Make `uow`'s next bump wait for `release`, setting `reached` first."""
    reached, release = asyncio.Event(), asyncio.Event()
    bump = uow.tenders.bump_state_version

    async def paused(tender_id: uuid.UUID) -> None:
        reached.set()
        await release.wait()
        await bump(tender_id)

    monkeypatch.setattr(uow.tenders, "bump_state_version", paused)
    return reached, release


async def _resolved(finding_id: uuid.UUID) -> bool:
    async with get_session() as session:
        return (
            await session.execute(
                select(PricingFinding.resolved).where(PricingFinding.id == finding_id)
            )
        ).scalar_one()


async def _approved(entry_id: uuid.UUID) -> bool:
    async with get_session() as session:
        return (
            await session.execute(
                select(ComplianceEntry.human_approved).where(ComplianceEntry.id == entry_id)
            )
        ).scalar_one()


async def _state_version(tender_id: uuid.UUID) -> int:
    async with get_session() as session:
        return (
            await session.execute(select(Tender.state_version).where(Tender.id == tender_id))
        ).scalar_one()


async def _upload(tender_id: uuid.UUID) -> None:
    async with UnitOfWork() as uow:
        await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="amendment.pdf",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        await uow.commit()


async def _new_finding(tender_id: uuid.UUID, sheet_id: uuid.UUID) -> None:
    """The commercial node's insert, in its own transaction."""
    async with UnitOfWork() as uow:
        await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note="Item 9 total is wrong.",
            content_fingerprint=uuid.uuid4().hex,
            pricing_document_id=sheet_id,
        )
        await uow.commit()


@pytest.mark.parametrize("insert", ["an upload", "the commercial node's new finding"])
async def test_an_insert_that_references_the_tender_does_not_wait_for_a_resolve(insert):
    """A resolve holds its tender until it commits. A document uploaded to
    the bid, or a finding the commercial node stores, meanwhile goes through:
    neither waits behind the Resolve click."""
    bid = await _bid()
    async with UnitOfWork() as holder:
        await commercial_service.resolve_pricing_finding_in_uow(
            holder, bid.findings[0], uuid.uuid4()
        )
        if insert == "an upload":
            task = asyncio.create_task(_upload(bid.tender_id))
        else:
            task = asyncio.create_task(_new_finding(bid.tender_id, bid.sheets[0]))
        await _finished_without_waiting(task, f"{insert} during a resolve")
        await holder.commit()
    assert await _resolved(bid.findings[0]) is True


async def test_two_resolves_on_two_sheets_of_one_bid_queue_at_the_tender_lock():
    """FOR NO KEY UPDATE still orders two resolves on one bid. Findings on
    two different sheets share no document lock, so the second resolve can
    only wait on the tender, and it waits there at its first lock, before it
    grounds anything."""
    bid = await _bid()
    user_id = uuid.uuid4()
    async with UnitOfWork() as holder:
        await commercial_service.resolve_pricing_finding_in_uow(holder, bid.findings[0], user_id)
        other = asyncio.create_task(
            commercial_service.resolve_pricing_finding(bid.findings[1], user_id)
        )
        await _waiting(other, "the second resolve never waited for the first")
        waiting_on = (await holder.session.execute(_WAITING_QUERY)).scalar_one()
        assert "FROM tenders" in waiting_on
        assert "FOR NO KEY UPDATE" in waiting_on
        await holder.commit()
    await asyncio.wait_for(other, timeout=10)
    assert await _resolved(bid.findings[0]) is True
    assert await _resolved(bid.findings[1]) is True


async def test_an_approval_and_a_resolve_on_one_bid_do_not_deadlock(monkeypatch):
    """The resolve holds its tender lock and is paused before its bump. The
    approval inserts its audit row (KEY SHARE on the tender) and waits at its
    own bump. The resolve's bump then takes nothing new. Had it to upgrade to
    FOR UPDATE, it would wait on the approval's KEY SHARE while the approval
    waited on it."""
    bid = await _bid()
    user_id = uuid.uuid4()
    async with UnitOfWork() as resolver:
        reached, release = _pause_before_bump(resolver, monkeypatch)
        resolve = asyncio.create_task(
            commercial_service.resolve_pricing_finding_in_uow(resolver, bid.findings[0], user_id)
        )
        try:
            await asyncio.wait_for(reached.wait(), timeout=10)
            approve = asyncio.create_task(
                compliance_service.approve_compliance_entry(
                    bid.entries[0], user_id, shown_status="fully_compliant"
                )
            )
            await _waiting(approve, "the approval never waited for the resolve")
        finally:
            release.set()
        await asyncio.wait_for(resolve, timeout=10)
        await resolver.commit()
    await asyncio.wait_for(approve, timeout=10)
    assert await _resolved(bid.findings[0]) is True
    assert await _approved(bid.entries[0]) is True


async def test_two_approvals_on_one_bid_do_not_deadlock(monkeypatch):
    """The deadlock on main. The first approval has inserted its audit row
    and is paused before its bump; the second inserts its own and bumps.
    With FOR UPDATE, the second's bump waited on the first's KEY SHARE, then
    the first's bump waited on the second's: Postgres failed one."""
    bid = await _bid()
    user_id = uuid.uuid4()
    before = await _state_version(bid.tender_id)
    async with UnitOfWork() as first:
        reached, release = _pause_before_bump(first, monkeypatch)
        one = asyncio.create_task(
            compliance_service.approve_compliance_entry_in_uow(
                first, bid.entries[0], user_id, shown_status="fully_compliant"
            )
        )
        try:
            await asyncio.wait_for(reached.wait(), timeout=10)
            two = asyncio.create_task(
                compliance_service.approve_compliance_entry(
                    bid.entries[1], user_id, shown_status="fully_compliant"
                )
            )
            await _finished_or_waiting(two, "the second approval")
        finally:
            release.set()
        outcomes = await asyncio.gather(
            asyncio.wait_for(one, timeout=10),
            asyncio.wait_for(two, timeout=10),
            return_exceptions=True,
        )
        errors = [o for o in outcomes if isinstance(o, BaseException)]
        assert errors == [], errors
        await first.commit()
    assert await _approved(bid.entries[0]) is True
    assert await _approved(bid.entries[1]) is True
    assert await _state_version(bid.tender_id) == before + 2


async def test_a_bump_counts_a_version_committed_after_its_tender_was_read(monkeypatch):
    """Approve reads the tender, then bumps it. Another transaction's bump
    that commits in between is counted: the approval writes the version
    after it. Before, the bump's locked read left the approval's copy of the
    tender as first read, and `+= 1` wrote the other's version again, so
    one of the two changes moved no version (observed on main)."""
    bid = await _bid()
    before = await _state_version(bid.tender_id)
    async with UnitOfWork() as approver:
        reached, release = asyncio.Event(), asyncio.Event()
        create_audit = approver.audit.create

        async def paused(*args, **kwargs):
            reached.set()
            await release.wait()
            return await create_audit(*args, **kwargs)

        monkeypatch.setattr(approver.audit, "create", paused)
        approve = asyncio.create_task(
            compliance_service.approve_compliance_entry_in_uow(
                approver, bid.entries[0], uuid.uuid4(), shown_status="fully_compliant"
            )
        )
        try:
            # The approval has read the tender and is paused before its audit
            # row and its bump.
            await asyncio.wait_for(reached.wait(), timeout=10)
            async with UnitOfWork() as other:
                await other.tenders.bump_state_version(bid.tender_id)
                await other.commit()
        finally:
            release.set()
        await asyncio.wait_for(approve, timeout=10)
        await approver.commit()
    assert await _state_version(bid.tender_id) == before + 2


async def _tender(tender_id: uuid.UUID) -> Tender:
    async with get_session() as session:
        return (await session.execute(select(Tender).where(Tender.id == tender_id))).scalar_one()


async def test_a_bid_edit_stores_both_the_field_and_the_new_version():
    """update_tender changes the bid and bumps it in one transaction. The
    bump's read refreshes the tender from the row (populate_existing), so an
    edit not yet written when it ran would be overwritten: both the edit and
    the new version must be stored."""
    bid = await _bid()
    before = await _state_version(bid.tender_id)
    await tender_service.update_tender(
        bid.tender_id, name="Renamed", submission_date=date(2027, 3, 1), status="submitted"
    )
    stored = await _tender(bid.tender_id)
    assert (stored.name, stored.submission_date, stored.status) == (
        "Renamed",
        date(2027, 3, 1),
        "submitted",
    )
    assert stored.state_version == before + 1


async def test_a_tender_change_not_yet_written_when_the_bump_reads_is_kept():
    """No caller today changes a tender and then bumps with the change still
    unwritten (update_tender bumps first, and update() flushes its own
    change). This holds the next one to it: the session's autoflush writes
    the change before the bump's locked read, so the refresh reads it back."""
    bid = await _bid()
    before = await _state_version(bid.tender_id)
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(bid.tender_id)
        assert tender is not None
        tender.name = "Changed before the bump"
        await uow.tenders.bump_state_version(bid.tender_id)
        assert tender.name == "Changed before the bump", "the bump's refresh overwrote it"
        await uow.commit()
    stored = await _tender(bid.tender_id)
    assert stored.name == "Changed before the bump"
    assert stored.state_version == before + 1
