"""Plan item 124 (the user's addition): readiness's reanalysis_not_applied
blocker. A knowledge selection change whose reanalysis failed left the
bid's compliance decisions, approvals and claim grounding not reflecting
it; readiness blocks while the item's latest failed claim has no completed
claim at a later state version.

Real readiness on real Postgres and Neo4j (the stale-graph blocker of a
never-projected bid shows too; only this blocker is looked at). Claims are
written as rows in the states the job and the reaper leave them in.
"""

import uuid

import pytest
from _helpers import register_org_and_tender
from sqlalchemy import func
from tenderos_backend.db.models.knowledge import KnowledgeItem, TenderKnowledgeSelection
from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import readiness_service

pytestmark = pytest.mark.asyncio


async def _bid(label: str) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh organization's tender. Returns (organization, tender)."""
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    return tender.organization_id, tender.id


async def _item(organization_id: uuid.UUID, title: str) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item = KnowledgeItem(
            organization_id=organization_id,
            item_type="capability",
            title=title,
            content_fingerprint=uuid.uuid4().hex,
        )
        uow.session.add(item)
        await uow.session.flush()
        await uow.commit()
        return item.id


async def _claim(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, state_version: int, status: str
) -> None:
    ended = {
        "running": {},
        "completed": {"completed_at": func.now()},
        "failed": {"failed_at": func.now(), "error_code": "GRAPH_STALE"},
    }[status]
    async with UnitOfWork() as uow:
        uow.session.add(
            ReanalysisClaim(
                tender_id=tender_id,
                knowledge_item_id=knowledge_item_id,
                state_version=state_version,
                status=status,
                claim_token=uuid.uuid4(),
                locked_until=func.now(),
                **ended,
            )
        )
        await uow.commit()


async def _selection(tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool) -> None:
    """The bid's selection row as the selection route leaves it."""
    async with UnitOfWork() as uow:
        uow.session.add(
            TenderKnowledgeSelection(
                tender_id=tender_id, knowledge_item_id=knowledge_item_id, selected=selected
            )
        )
        await uow.commit()


async def _not_applied(tender_id: uuid.UUID) -> list[dict]:
    result = await readiness_service.evaluate_readiness(tender_id)
    return [b for b in result.blockers if b["category"] == readiness_service.REANALYSIS_NOT_APPLIED]


_FAILED = (
    "A selection change of {name} was not applied to this bid: its reanalysis failed, so "
    "compliance decisions, approvals and claim grounding may not reflect it. "
)


async def test_a_failed_reanalysis_of_a_selected_item_says_deselect_then_select():
    """B1. The blocker names the item, says it is selected, and gives the
    re-run's two steps in the order that leaves it selected; a failed claim
    of another bid does not reach this one."""
    org, tender = await _bid("not-applied")
    item = await _item(org, "ISO 27001 certificate")
    await _selection(tender, item, selected=True)
    _other_org, other_tender = await _bid("not-applied-other")
    await _claim(tender, item, 5, "failed")
    await _claim(other_tender, uuid.uuid4(), 5, "failed")

    [blocker] = await _not_applied(tender)

    assert (blocker["target_type"], blocker["target_id"]) == ("knowledge_item", str(item))
    assert blocker["message"] == _FAILED.format(name="'ISO 27001 certificate'") + (
        "'ISO 27001 certificate' is selected for this bid. To re-run it, deselect it and then "
        "select it again on the Knowledge selections page."
    )


async def test_a_failed_reanalysis_of_a_deselected_item_says_select_then_deselect():
    """B6 (PR 40's review). Someone who deselected the item must end with it
    deselected: reselecting it alone would clear the blocker but leave it
    selected against their intent. A deselected item keeps its row
    (selected false), so the row's presence alone is not a selection."""
    org, tender = await _bid("not-applied-deselected")
    item = await _item(org, "Past project: harbour dredging")
    await _selection(tender, item, selected=False)
    await _claim(tender, item, 5, "failed")

    [blocker] = await _not_applied(tender)

    assert blocker["message"] == _FAILED.format(name="'Past project: harbour dredging'") + (
        "'Past project: harbour dredging' is deselected for this bid. To re-run it, select it "
        "and then deselect it again on the Knowledge selections page."
    )


async def test_a_later_completed_reanalysis_of_the_same_item_clears_it():
    """B2."""
    org, tender = await _bid("not-applied-cleared")
    item = await _item(org, "Past project: metro signalling")
    await _claim(tender, item, 5, "failed")
    await _claim(tender, item, 7, "completed")

    assert await _not_applied(tender) == []


async def test_a_completed_reanalysis_of_another_item_does_not_clear_it():
    """B3."""
    org, tender = await _bid("not-applied-other-item")
    failed_item = await _item(org, "Employee: site engineer")
    other_item = await _item(org, "Capability statement")
    await _claim(tender, failed_item, 5, "failed")
    await _claim(tender, other_item, 7, "completed")

    assert [b["target_id"] for b in await _not_applied(tender)] == [str(failed_item)]


async def test_only_a_completed_reanalysis_after_the_failure_clears_it():
    """B4. A completed claim at an earlier version, or a later one still
    running, leaves the failure unapplied."""
    org, tender = await _bid("not-applied-order")
    item = await _item(org, "Employee: project manager")
    await _claim(tender, item, 3, "completed")
    await _claim(tender, item, 5, "failed")
    await _claim(tender, item, 8, "running")

    assert [b["target_id"] for b in await _not_applied(tender)] == [str(item)]


async def test_a_deleted_item_is_named_by_its_id_and_points_to_the_waiver():
    """B5. A claim's item has no foreign key (it outlives the item). An item
    that no longer exists cannot be selected, so the message says it cannot
    be re-run and that the final gate's waiver is the way on (PR 40's
    review)."""
    _org, tender = await _bid("not-applied-deleted")
    gone = uuid.uuid4()
    await _claim(tender, gone, 5, "failed")

    [blocker] = await _not_applied(tender)

    assert blocker["message"] == _FAILED.format(name=f"knowledge item {gone}") + (
        "The item no longer exists, so its reanalysis cannot be re-run. To proceed, approve the "
        "final gate with Waive outstanding blockers ticked."
    )
