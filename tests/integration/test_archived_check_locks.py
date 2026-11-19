"""Plan item 59, fix 8 (PR 20): the two archived checks read the knowledge
item FOR SHARE.

Verifying an employee and selecting one for a role each refuse an employee
removed from the roster (knowledge_items.status 'archived'). The check was an
unlocked read, so an archive that had written but not committed was not
seen: the verify or select went on and committed for an employee archived a
moment later, and an archive arriving after the check did not wait for it.
Now each reads the item FOR SHARE and holds it to commit.

FOR SHARE is the weakest mode that conflicts with the archive's UPDATE (FOR
NO KEY UPDATE); FOR KEY SHARE would not, and the race would stay. It is safe
from upgrades because neither transaction writes the knowledge item: verify
writes the employees row, the select writes role_matches.

Lock order: verify takes the employee, then the item; the select takes the
role match, then the item; each then bumps the bid's tenders. The archive
paths take the item (and, in the employee-master import, the employee
first), then the tenders. No path holds a tender before an item.
"""

import asyncio
import uuid

import pytest
from _helpers import register_org_and_tender
from _lock_waits import first_then_second, pause_once
from sqlalchemy import select
from tenderos_backend.db.models.knowledge import Employee, KnowledgeItem
from tenderos_backend.db.models.talent import RoleMatch
from tenderos_backend.db.repositories.audit import AuditRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import knowledge_service, talent_service
from tenderos_backend.domain.services.knowledge_service import EmployeeArchivedError
from tenderos_backend.domain.services.talent_service import ArchivedEmployeeSelectionError

pytestmark = pytest.mark.asyncio


async def _bid_with_an_employee() -> dict[str, uuid.UUID]:
    """A bid selecting an unverified employee, with a role the employee
    matches."""
    email = f"archived-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} bid")
    org, actor = registered.organization.id, registered.user.id
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=org,
            name="Sam Example",
            content_fingerprint=uuid.uuid4().hex,
            title="Sam Example",
            current_title="Engineer",
        )
        await uow.knowledge.upsert_selection(tender.id, item.id, True, actor)
        role = await uow.talent.create_role_requirement(
            tender_id=tender.id, role_name="Lead", content_fingerprint=uuid.uuid4().hex
        )
        match = await uow.talent.create_role_match(role.id, item.id)
        await uow.commit()
    return {"org": org, "actor": actor, "employee": item.id, "match": match.id}


async def _status(item_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(KnowledgeItem.status).where(KnowledgeItem.id == item_id)
            )
        ).scalar_one()


async def _review_status(item_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(Employee.profile_review_status).where(Employee.knowledge_item_id == item_id)
            )
        ).scalar_one()


async def _selected(match_id: uuid.UUID) -> bool:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(RoleMatch.human_selected).where(RoleMatch.id == match_id)
            )
        ).scalar_one()


def _archive(ids):
    return lambda: knowledge_service.patch_knowledge_item(
        ids["employee"], ids["org"], ids["actor"], status="archived"
    )


def _verify(ids):
    return lambda: knowledge_service.verify_employee_profile(
        ids["employee"], ids["org"], ids["actor"], True
    )


def _select(ids):
    return lambda: talent_service.select_candidate(ids["match"], ids["actor"])


def _share_on_items(seen: list[str]) -> None:
    assert any("FROM knowledge_items" in q and "FOR SHARE" in q for q in seen), seen


async def test_a_verify_waits_for_an_archive_and_then_refuses(monkeypatch):
    ids = await _bid_with_an_employee()
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(_archive(ids)())
    archived, verified, seen = await first_then_second(
        first, reached, release, _verify(ids), "the verify never waited for the archive"
    )
    assert not isinstance(archived, BaseException), archived
    assert isinstance(verified, EmployeeArchivedError), verified
    _share_on_items(seen)
    assert await _review_status(ids["employee"]) != "verified"


async def test_an_archive_waits_for_a_verify_that_has_checked(monkeypatch):
    """The verify has checked the item and written the employee, and holds
    its transaction: the archive waits, then archives."""
    ids = await _bid_with_an_employee()
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(_verify(ids)())
    verified, archived, _seen = await first_then_second(
        first, reached, release, _archive(ids), "the archive never waited for the verify"
    )
    assert not isinstance(verified, BaseException), verified
    assert not isinstance(archived, BaseException), archived
    assert await _review_status(ids["employee"]) == "verified"
    assert await _status(ids["employee"]) == "archived"


async def test_a_select_waits_for_an_archive_and_then_refuses(monkeypatch):
    ids = await _bid_with_an_employee()
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(_archive(ids)())
    archived, selected, seen = await first_then_second(
        first, reached, release, _select(ids), "the select never waited for the archive"
    )
    assert not isinstance(archived, BaseException), archived
    assert isinstance(selected, ArchivedEmployeeSelectionError), selected
    _share_on_items(seen)
    assert await _selected(ids["match"]) is False


async def test_an_archive_waits_for_a_select_that_has_checked(monkeypatch):
    ids = await _bid_with_an_employee()
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(_select(ids)())
    selected, archived, _seen = await first_then_second(
        first, reached, release, _archive(ids), "the archive never waited for the select"
    )
    assert not isinstance(selected, BaseException), selected
    assert not isinstance(archived, BaseException), archived
    assert await _selected(ids["match"]) is True
    assert await _status(ids["employee"]) == "archived"
