"""Plan item 59, PR 19: a proposal re-draft against a person editing the same
section.

A re-draft rewrites the latest version of a section in place while it is
still a draft, and makes a new version once a person has moved it to
review. It judged "still a draft" on an unlocked read: a person's edit
committing in between was overwritten by the model's text, under the
person's review status. The in-place rewrite is now conditional on the
section still being a draft; refused, the re-draft makes a new version, as
it does for a section already in review, and the person's edit stays.

The race runs in one order, with no sleeps: the person's edit is held open,
the re-draft is started and the test waits until Postgres shows it waiting
on the section's row lock, then the edit commits.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.proposal import ProposalSection
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import proposal_service

pytestmark = pytest.mark.asyncio

_SECTION_UPDATE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE 'UPDATE proposal_sections%'"
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


async def _draft_section() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"Proposal Race {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Proposal Race", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        tender_id = tender.id
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(
            tender_id=tender_id, section_name="Approach", content="First draft.", version=1
        )
        await uow.commit()
        return tender_id, section.id


async def _versions(tender_id: uuid.UUID) -> list[tuple[int, str, str]]:
    async with get_session() as session:
        rows = await session.execute(
            select(ProposalSection.version, ProposalSection.status, ProposalSection.content)
            .where(ProposalSection.tender_id == tender_id)
            .order_by(ProposalSection.version)
        )
        return [(r[0], r[1], r[2]) for r in rows.all()]


async def test_a_redraft_never_overwrites_an_edit_that_landed_while_it_ran():
    tender_id, section_id = await _draft_section()
    redraft = ProposalSectionDraft(content="Second draft.", claims=[])

    async with UnitOfWork() as person:
        # What update_section_content writes, held open.
        await person.proposal.update_proposal_section(
            section_id, content="The person's edit.", status="review"
        )
        task = asyncio.create_task(
            proposal_service.persist_section_and_claims(tender_id, "Approach", redraft, [], {})
        )
        await _waiting_on(_SECTION_UPDATE_WAITING, task, "the re-draft never waited on the section")
        await person.commit()

    written = await asyncio.wait_for(task, timeout=10)
    assert written.version == 2
    assert await _versions(tender_id) == [
        (1, "review", "The person's edit."),
        (2, "draft", "Second draft."),
    ]


async def test_a_redraft_of_an_untouched_draft_still_rewrites_it_in_place():
    """Guard (passes before this fix too): with no edit, the draft is
    rewritten in place, as an automated retry should be."""
    tender_id, section_id = await _draft_section()

    written = await proposal_service.persist_section_and_claims(
        tender_id, "Approach", ProposalSectionDraft(content="Second draft.", claims=[]), [], {}
    )

    assert written.id == section_id
    assert await _versions(tender_id) == [(1, "draft", "Second draft.")]


async def test_a_redraft_that_had_to_make_a_new_version_bumps_the_tender():
    """A new version is a new graph node, so the re-draft that had to make
    one bumps the tender's state version."""
    tender_id, section_id = await _draft_section()
    async with get_session() as session:
        before = (
            await session.execute(
                text("SELECT state_version FROM tenders WHERE id = :id"), {"id": tender_id}
            )
        ).scalar_one()

    async with UnitOfWork() as person:
        await person.proposal.update_proposal_section(
            section_id, content="The person's edit.", status="review"
        )
        task = asyncio.create_task(
            proposal_service.persist_section_and_claims(
                tender_id,
                "Approach",
                ProposalSectionDraft(content="Second draft.", claims=[]),
                [],
                {},
            )
        )
        await _waiting_on(_SECTION_UPDATE_WAITING, task, "the re-draft never waited on the section")
        await person.commit()
    await asyncio.wait_for(task, timeout=10)

    async with get_session() as session:
        after = (
            await session.execute(
                text("SELECT state_version FROM tenders WHERE id = :id"), {"id": tender_id}
            )
        ).scalar_one()
    assert after == before + 1
