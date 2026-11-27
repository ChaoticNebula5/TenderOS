"""Plan item 126, addition 4: an extraction persisting for a bid while a
manual merge or split of the same bid commits.

Without a common lock, an extraction could find an original by its key, then
a merge delete it: the extraction would return the deleted row, or its
citation would fail the foreign key. Extraction's persist, merge and split
now take the bid's requirement-set lock first (an advisory lock), so they run
one at a time. Each order runs with no sleeps, by the shape in _lock_waits.py:
the first transaction is held open at a chosen point, the second is started
and the test waits until Postgres shows it waiting on a lock, then the first
goes on.

Real Postgres.
"""

import asyncio
import uuid

import pytest
from _lock_waits import first_then_second, pause_once
from sqlalchemy import select
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.requirement import Requirement, RequirementCitation
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories.requirements import RequirementRepository
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import requirement_service
from tenderos_backend.domain.services.requirement_service import (
    RequirementDraft,
    persist_extraction_results,
)

pytestmark = pytest.mark.asyncio

KEPT = "The bidder shall be registered for VAT."
MERGED_AWAY = "The bidder shall hold a valid tax clearance certificate."


async def _bid() -> dict:
    """A bid with chunks 0 and 1 carrying MERGED_AWAY between them, and
    chunk 5 holding KEPT. Both requirements extracted, MERGED_AWAY from
    chunk 0 only, so its copy in chunk 1 is still to be found."""
    async with get_session() as session:
        org = Organization(name=f"Merge Race {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Merge Race")
        session.add(tender)
        await session.commit()
        org_id, tender_id = org.id, tender.id
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
        chunks = {}
        for index, lines in (
            (0, ["Section 2.", MERGED_AWAY]),
            (1, [MERGED_AWAY, "Bids are valid for ninety days."]),
            (5, ["Section 9.", KEPT]),
        ):
            body = "\n".join(lines)
            chunk = await uow.documents.create_chunk(
                document_id=document.id,
                chunk_index=index,
                chunk_text=body,
                normalized_text=" ".join(body.split()),
                content_hash=uuid.uuid4().hex,
                page_num=1,
            )
            chunks[index] = chunk.id
        await uow.commit()
    ids = {"org": org_id, "tender": tender_id, "document": document.id, "chunks": chunks}
    kept, merged_away = (
        r.id
        for r in await persist_extraction_results(
            tender_id, [_draft(ids, KEPT, 5), _draft(ids, MERGED_AWAY, 0)]
        )
    )
    ids.update(kept=kept, merged_away=merged_away)
    return ids


def _draft(ids: dict, wording: str, index: int) -> RequirementDraft:
    return RequirementDraft(
        requirement_text=wording,
        category="eligibility",
        mandatory=True,
        source_chunk_id=ids["chunks"][index],
        document_id=ids["document"],
    )


async def _merge(ids: dict) -> Requirement:
    return await requirement_service.merge_requirements(
        [ids["kept"], ids["merged_away"]], "Merged.", "eligibility", True, ids["org"], uuid.uuid4()
    )


async def _rows(tender_id: uuid.UUID) -> set[uuid.UUID]:
    async with get_session() as session:
        result = await session.execute(
            select(Requirement.id).where(Requirement.tender_id == tender_id)
        )
        return set(result.scalars().all())


async def _cited(requirement_id: uuid.UUID) -> set[uuid.UUID]:
    async with get_session() as session:
        result = await session.execute(
            select(RequirementCitation.chunk_id).where(
                RequirementCitation.requirement_id == requirement_id
            )
        )
        return set(result.scalars().all())


async def test_an_extraction_during_a_merge_waits_and_finds_the_survivor(monkeypatch):
    """The merge is held after moving the keys, before it commits. The
    extraction's exact key for MERGED_AWAY in chunk 0 still names the
    original in the committed data; it waits for the merge, then finds the
    survivor."""
    ids = await _bid()
    reached, release = pause_once(monkeypatch, RequirementRepository, "move_keys", after=True)

    merge, extraction, waiting_sql = await first_then_second(
        asyncio.create_task(_merge(ids)),
        reached,
        release,
        lambda: persist_extraction_results(ids["tender"], [_draft(ids, MERGED_AWAY, 0)]),
        "the extraction did not wait for the merge",
    )

    assert not isinstance(merge, BaseException), merge
    assert not isinstance(extraction, BaseException), extraction
    assert [row.id for row in extraction] == [ids["kept"]]
    assert await _rows(ids["tender"]) == {ids["kept"]}
    assert any("pg_advisory_xact_lock" in sql for sql in waiting_sql), waiting_sql


async def test_a_merge_during_an_extraction_waits_and_keeps_its_citation(monkeypatch):
    """The extraction found MERGED_AWAY's copy in chunk 1 (an overlap match)
    and is held before citing it. The merge waits, then moves the new
    citation to the survivor."""
    ids = await _bid()
    reached, release = pause_once(monkeypatch, RequirementRepository, "add_citation", after=False)

    extraction, merge, waiting_sql = await first_then_second(
        asyncio.create_task(
            persist_extraction_results(ids["tender"], [_draft(ids, MERGED_AWAY, 1)])
        ),
        reached,
        release,
        lambda: _merge(ids),
        "the merge did not wait for the extraction",
    )

    assert not isinstance(extraction, BaseException), extraction
    assert not isinstance(merge, BaseException), merge
    assert [row.id for row in extraction] == [ids["merged_away"]]
    assert await _rows(ids["tender"]) == {ids["kept"]}
    assert ids["chunks"][1] in await _cited(ids["kept"])
    assert any("pg_advisory_xact_lock" in sql for sql in waiting_sql), waiting_sql


async def test_a_split_during_an_extraction_waits(monkeypatch):
    """A split changes what extraction reads and writes: it rewrites the
    original in place (the first part, which keeps the original's keys and
    citations) and creates parts with their own keys, citations, claim links
    and role sources. Between an extraction's key lookup and its citation,
    the row it found could change under it, so the split waits for the
    extraction's commit. (Display numbers come from the bid's counter, which
    is safe on its own.)"""
    ids = await _bid()
    reached, release = pause_once(monkeypatch, RequirementRepository, "add_citation", after=False)

    extraction, split, waiting_sql = await first_then_second(
        asyncio.create_task(
            persist_extraction_results(ids["tender"], [_draft(ids, MERGED_AWAY, 1)])
        ),
        reached,
        release,
        lambda: requirement_service.split_requirement(
            ids["kept"],
            [{"requirement_text": "Part one."}, {"requirement_text": "Part two."}],
            ids["org"],
            uuid.uuid4(),
        ),
        "the split did not wait for the extraction",
    )

    assert not isinstance(extraction, BaseException), extraction
    assert not isinstance(split, BaseException), split
    assert len(await _rows(ids["tender"])) == 3
    assert any("pg_advisory_xact_lock" in sql for sql in waiting_sql), waiting_sql
