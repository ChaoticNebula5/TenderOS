"""Plan item 59, D8 (PR 22): a re-draft merges a reused claim's factual flag
towards stricter.

A re-draft of a section still in draft finds each claim again by its
fingerprint: the section, the claim's text and its type. is_factual is not in
the key, so a re-draft whose model now marks a stored narrative claim factual
reused the narrative row and grounded it as narrative: "not_required", a
factual claim with no evidence passing every check that asks for evidence.
Now the flag is the stored value or the draft's, under the claim's lock,
before the claim is regrounded: narrative to factual, never back. A claim
once marked factual stays factual through re-drafts; that fails safe, asking
for evidence or review.

Of the other columns the key leaves out, none changes what a claim must
prove (the PR body has one line each).
"""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.db.models.proposal import ProposalClaim, ProposalSection
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import proposal_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio

_TEXT = "We delivered three national payment systems."


async def _bid() -> uuid.UUID:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Claim factual merge org",
    )
    tender = await create_tender(
        organization_id=registered.organization.id,
        name="Claim factual merge bid",
        created_by=registered.user.id,
        submission_date=date(2027, 1, 15),
    )
    return tender.id


async def _claims(tender_id: uuid.UUID) -> list[tuple[bool, str]]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(ProposalClaim.is_factual, ProposalClaim.grounding_status)
            .join(ProposalSection, ProposalSection.id == ProposalClaim.section_id)
            .where(ProposalSection.tender_id == tender_id)
        )
        return [(r.is_factual, r.grounding_status) for r in rows.all()]


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(select(Tender.state_version).where(Tender.id == tender_id))
        ).scalar_one()


async def _draft(tender_id: uuid.UUID, is_factual: bool) -> None:
    """One draft of the section, its one claim with no evidence."""
    claim = ProposalClaimDraft(claim_text=_TEXT, claim_type="experience", is_factual=is_factual)
    await proposal_service.persist_section_and_claims(
        tender_id, "Experience", ProposalSectionDraft(content="Draft.", claims=[claim]), [], {}
    )


async def test_a_redraft_marking_a_narrative_claim_factual_makes_it_factual_and_unsupported():
    """The reproduction: on main the stored narrative flag won, and the claim
    read (False, not_required)."""
    tender_id = await _bid()
    await _draft(tender_id, is_factual=False)
    assert await _claims(tender_id) == [(False, "not_required")]
    start = await _version(tender_id)

    await _draft(tender_id, is_factual=True)

    assert await _claims(tender_id) == [(True, "unsupported")]
    assert await _version(tender_id) == start + 1, "is_factual and the status are on the node"


async def test_a_claim_once_factual_stays_factual_when_a_redraft_calls_it_narrative():
    """Never back: a later draft calling the same claim narrative does not
    exempt it from evidence."""
    tender_id = await _bid()
    await _draft(tender_id, is_factual=True)
    assert await _claims(tender_id) == [(True, "unsupported")]
    start = await _version(tender_id)

    await _draft(tender_id, is_factual=False)

    assert await _claims(tender_id) == [(True, "unsupported")]
    assert await _version(tender_id) == start, "nothing on the node changed"


async def test_a_narrative_claim_drafted_narrative_again_stays_narrative():
    """The control: no flag to merge."""
    tender_id = await _bid()
    await _draft(tender_id, is_factual=False)
    await _draft(tender_id, is_factual=False)
    assert await _claims(tender_id) == [(False, "not_required")]
