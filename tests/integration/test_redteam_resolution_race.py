"""Plan item 59, PR 19: a red-team pass against a person resolving a finding.

A pass retires the open findings it no longer detects and reopens a
system-retired finding it detects again. Both judged an unlocked read: a
person's resolve committing in between was overwritten (the retire replaced
the person's note and source with the system's; the reopen undid the
resolve). Both writes are now conditional: retire only a finding still
open, reopen only one the system retired. A person's resolve stays
unconditional, so a person's later word wins.

Each race runs in one order, with no sleeps: the person's resolve is held
open, the pass is started and the test waits until Postgres shows it waiting
on the finding's row lock, then the resolve commits.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.redteam import RedteamFinding
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import redteam_service
from tenderos_backend.domain.services.redteam_checks import RedteamFindingDraft

pytestmark = pytest.mark.asyncio

_FINDING_UPDATE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE 'UPDATE redteam_findings%'"
)


async def _waiting_on(query, task: asyncio.Task, what: str) -> None:
    for _ in range(200):
        if task.done():
            break
        async with get_session() as session:
            if (await session.execute(query)).scalar_one() > 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


def _draft() -> RedteamFindingDraft:
    return RedteamFindingDraft(
        target_type="requirement",
        target_id=uuid.uuid4(),
        finding_category="unsupported_claim",
        severity="major",
        finding_text="A claim cites nothing.",
        violated_condition="claim_has_evidence",
    )


async def _finding(*, retired_by_system: bool) -> tuple[uuid.UUID, uuid.UUID, RedteamFindingDraft]:
    async with get_session() as session:
        org = Organization(name=f"Red Team Race {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Red Team Race", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        tender_id = tender.id
    draft = _draft()
    async with UnitOfWork() as uow:
        finding = await redteam_service.persist_finding_draft(uow, tender_id, draft)
        if retired_by_system:
            await uow.redteam.auto_retire_undetected_findings(tender_id, [])
        await uow.commit()
        return tender_id, finding.id, draft


async def _state(finding_id: uuid.UUID) -> tuple[bool, str | None, str | None]:
    async with get_session() as session:
        finding = (
            await session.execute(select(RedteamFinding).where(RedteamFinding.id == finding_id))
        ).scalar_one()
        return finding.resolved, finding.resolution_source, finding.resolution_note


async def test_a_pass_does_not_retire_a_finding_a_person_resolved_meanwhile():
    tender_id, finding_id, _draft_ = await _finding(retired_by_system=False)

    async def retire() -> int:
        async with UnitOfWork() as uow:
            retired = await uow.redteam.auto_retire_undetected_findings(tender_id, [])
            await uow.commit()
            return len(retired)

    async with UnitOfWork() as person:
        await person.redteam.resolve_finding(finding_id, "Fixed in section 3.")
        task = asyncio.create_task(retire())
        await _waiting_on(_FINDING_UPDATE_WAITING, task, "the retire never waited on the finding")
        await person.commit()

    assert await asyncio.wait_for(task, timeout=10) == 0
    assert await _state(finding_id) == (True, "human", "Fixed in section 3.")


async def test_a_pass_does_not_reopen_a_finding_a_person_resolved_meanwhile():
    """The finding was retired by an earlier pass, a person resolves it, and
    this pass detects it again: it stays the person's."""
    tender_id, finding_id, draft = await _finding(retired_by_system=True)
    assert (await _state(finding_id))[:2] == (True, "system")

    async def detect_again() -> bool:
        async with UnitOfWork() as uow:
            finding = await redteam_service.persist_finding_draft(uow, tender_id, draft)
            await uow.commit()
            return finding.resolved

    async with UnitOfWork() as person:
        await person.redteam.resolve_finding(finding_id, "Accepted risk.")
        task = asyncio.create_task(detect_again())
        await _waiting_on(_FINDING_UPDATE_WAITING, task, "the reopen never waited on the finding")
        await person.commit()

    assert await asyncio.wait_for(task, timeout=10) is True
    assert await _state(finding_id) == (True, "human", "Accepted risk.")


async def test_a_pass_still_retires_and_reopens_its_own_findings():
    """Guard (passes before this fix too): with no person involved, a pass
    retires what it no longer detects and reopens what comes back."""
    tender_id, finding_id, draft = await _finding(retired_by_system=False)
    async with UnitOfWork() as uow:
        retired = await uow.redteam.auto_retire_undetected_findings(tender_id, [])
        await uow.commit()
    assert [finding.id for finding in retired] == [finding_id]
    assert (await _state(finding_id))[:2] == (True, "system")

    async with UnitOfWork() as uow:
        reopened = await redteam_service.persist_finding_draft(uow, tender_id, draft)
        await uow.commit()
    assert reopened.resolved is False
    assert await _state(finding_id) == (False, None, None)
