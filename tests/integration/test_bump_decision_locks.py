"""Plan item 59, fix 7 and D1 to D3 (PR 20): a bump decision reads its row
locked before it takes "before".

Each of these compares a "before" with its "after" to decide whether to
bump the bid's state version: an item, a profile or a bid edit, a select, a
verify, the employee-master import's two branches, and (D4 to D7) a
requirement edit, a proposal section edit, a claim's regrounding and a
re-draft's update of an existing claim.
"Before" came from an unlocked read. Two edits of one projected field at once: A changes X to Y
and holds its transaction; B reads X, sets X and, since SQLAlchemy writes
nothing for an attribute set to the value it read, writes nothing at all. A
then commits Y. B's edit is lost and its audit row says it wrote X. Now each
reads its row FOR NO KEY UPDATE first, so B waits for A, reads Y, writes X
and bumps.

FOR NO KEY UPDATE, not FOR UPDATE: it is the lock the UPDATE itself takes,
so the write that follows upgrades nothing, and it lets an insert of a row
that references the locked one through (its foreign key check takes KEY
SHARE). Fourteen foreign keys reference knowledge_items, so FOR UPDATE would
make every evidence link, selection or role match on the item wait for an
edit of it (the last test).

Lock order on every path: the edited row (the employee before its knowledge
item where a path takes both), then the bid's tenders in sorted order. The
requirement, the section and the claim each belong to one bid, so each path
takes that row, then its one tender. A re-draft takes its section, then its
existing claims in the draft's order, then the tender: it reaches existing
claims only through the in-place rewrite, which holds the section's lock,
so two re-drafts of one section never hold its claims at once, and a
regrounding holds one claim per transaction.

Steps: A stops inside its transaction (pause_once, after its write), B is
started and must wait on a lock, A is released (_lock_waits.first_then_second).
"""

import asyncio
import uuid
from dataclasses import dataclass

import pytest
from _helpers import register_org_and_tender
from _lock_waits import finished_without_waiting, first_then_second, pause_once, waiting
from sqlalchemy import select
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.knowledge import Employee, KnowledgeItem
from tenderos_backend.db.models.proposal import ProposalClaim, ProposalSection
from tenderos_backend.db.models.requirement import Requirement
from tenderos_backend.db.models.talent import RoleMatch
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories.audit import AuditRepository
from tenderos_backend.db.repositories.knowledge import KnowledgeRepository
from tenderos_backend.db.repositories.tenders import TenderRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    knowledge_service,
    proposal_service,
    requirement_service,
    talent_service,
    tender_service,
)

pytestmark = pytest.mark.asyncio


@dataclass
class _Bid:
    org: uuid.UUID
    actor: uuid.UUID
    tender: uuid.UUID
    name: str


async def _bid() -> _Bid:
    email = f"bump-locks-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} bid")
    return _Bid(registered.organization.id, registered.user.id, tender.id, tender.name)


async def _selected_employee(bid: _Bid) -> uuid.UUID:
    """An employee the bid selects, so a projected change of theirs bumps it."""
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=bid.org,
            name="Pat Example",
            content_fingerprint=uuid.uuid4().hex,
            title="Pat Example",
            current_title="Engineer",
        )
        await uow.knowledge.upsert_selection(bid.tender, item.id, True, bid.actor)
        await uow.commit()
        return item.id


async def _selected_certificate(bid: _Bid) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_certification(
            organization_id=bid.org, title="ISO 27001", content_fingerprint=uuid.uuid4().hex
        )
        await uow.knowledge.upsert_selection(bid.tender, item.id, True, bid.actor)
        await uow.commit()
        return item.id


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(select(Tender.state_version).where(Tender.id == tender_id))
        ).scalar_one()


async def _value(column, key_column, key: uuid.UUID):
    async with UnitOfWork() as uow:
        return (await uow.session.execute(select(column).where(key_column == key))).scalar_one()


async def _audit_befores(target_id: uuid.UUID, action: str) -> list[dict]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(AuditEvent.before)
            .where(AuditEvent.target_id == target_id, AuditEvent.action == action)
            .order_by(AuditEvent.created_at)
        )
        return [before or {} for before in rows.scalars().all()]


def _no_errors(*outcomes) -> None:
    errors = [o for o in outcomes if isinstance(o, BaseException)]
    assert errors == [], errors


def _locked_read(seen: list[str], table: str) -> None:
    """B waits at its locked read of `table`, FOR NO KEY UPDATE."""
    assert any(f"FROM {table}" in q and "FOR NO KEY UPDATE" in q for q in seen), seen


async def test_two_item_edits_each_take_their_before_under_the_lock(monkeypatch):
    """patch_knowledge_item: a certificate's status, expired and back."""
    bid = await _bid()
    cert = await _selected_certificate(bid)
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        knowledge_service.patch_knowledge_item(cert, bid.org, bid.actor, status="expired")
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: knowledge_service.patch_knowledge_item(cert, bid.org, bid.actor, status="active"),
        "the second item edit never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "knowledge_items")
    assert await _value(KnowledgeItem.status, KnowledgeItem.id, cert) == "active"
    assert await _version(bid.tender) == start + 2
    befores = await _audit_befores(cert, "knowledge_item_edit")
    assert [b["status"] for b in befores] == ["active", "expired"]


async def test_two_profile_edits_each_take_their_before_under_the_lock(monkeypatch):
    """patch_employee_profile: the name, changed and back. D3: the employee's
    lock is FOR NO KEY UPDATE, as every other path's."""
    bid = await _bid()
    employee = await _selected_employee(bid)
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        knowledge_service.patch_employee_profile(employee, bid.org, bid.actor, name="Pat Q.")
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: knowledge_service.patch_employee_profile(
            employee, bid.org, bid.actor, name="Pat Example"
        ),
        "the second profile edit never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "employees")
    assert await _value(Employee.name, Employee.knowledge_item_id, employee) == "Pat Example"
    assert await _version(bid.tender) == start + 2
    befores = await _audit_befores(employee, "employee_profile_edit")
    assert [b["name"] for b in befores] == ["Pat Example", "Pat Q."]


async def test_two_bid_edits_each_take_their_before_under_the_lock(monkeypatch):
    """update_tender: the bid's name, changed and back. It bumps before it
    writes, so A is stopped after its update instead."""
    bid = await _bid()
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, TenderRepository, "update", after=True)
    first = asyncio.create_task(tender_service.update_tender(bid.tender, name="Renamed"))
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: tender_service.update_tender(bid.tender, name=bid.name),
        "the second bid edit never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "tenders")
    assert await _value(Tender.name, Tender.id, bid.tender) == bid.name
    assert await _version(bid.tender) == start + 2


async def _match(bid: _Bid, employee: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        role = await uow.talent.create_role_requirement(
            tender_id=bid.tender, role_name="Lead", content_fingerprint=uuid.uuid4().hex
        )
        match = await uow.talent.create_role_match(role.id, employee)
        await uow.commit()
        return match.id


async def test_a_select_and_a_deselect_each_take_their_before_under_the_lock(monkeypatch):
    """_set_human_selected_in_uow: A selects, B deselects."""
    bid = await _bid()
    employee = await _selected_employee(bid)
    match = await _match(bid, employee)
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(talent_service.select_candidate(match, bid.actor))
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: talent_service.deselect_candidate(match, bid.actor),
        "the deselect never waited for the select",
    )
    _no_errors(one, two)
    _locked_read(seen, "role_matches")
    assert await _value(RoleMatch.human_selected, RoleMatch.id, match) is False
    assert await _version(bid.tender) == start + 2
    befores = await _audit_befores(match, "role_deselect")
    assert [b["human_selected"] for b in befores] == [True]


async def test_two_verifies_each_take_their_before_under_the_lock(monkeypatch):
    """D1, verify_employee_profile's own bump decision: a verified profile
    made eligible (A), then not (B). On main B's write of eligibility was
    lost: its UPDATE carried only the new verification time."""
    bid = await _bid()
    employee = await _selected_employee(bid)
    await knowledge_service.verify_employee_profile(employee, bid.org, bid.actor, False)
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        knowledge_service.verify_employee_profile(employee, bid.org, bid.actor, True)
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: knowledge_service.verify_employee_profile(employee, bid.org, bid.actor, False),
        "the second verify never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "employees")
    eligible = await _value(Employee.eligible_for_bid, Employee.knowledge_item_id, employee)
    assert eligible is False
    assert await _version(bid.tender) == start + 2
    befores = await _audit_befores(employee, "employee_verify")
    assert [b["eligible_for_bid"] for b in befores][-2:] == [False, True]


async def _import_batch(bid: _Bid) -> uuid.UUID:
    async with UnitOfWork() as uow:
        batch = await uow.knowledge.create_import_batch(
            organization_id=bid.org,
            import_type="employee_master_csv",
            source_document_id=None,
            created_by=bid.actor,
        )
        await uow.commit()
        return batch.id


def _master_row(number: int, role: str, status: str = "active") -> EmployeeMasterRow:
    return EmployeeMasterRow(
        row_number=number, employee_code="EMP-59", name="Robin Row", role=role, status=status
    )


async def test_a_master_row_for_an_employee_on_file_takes_its_before_under_the_lock(monkeypatch):
    """The employee-master DUPLICATE branch: a profile edit makes the
    employee a Principal Engineer (A); the sheet's row says Engineer (B)."""
    bid = await _bid()
    batch = await _import_batch(bid)
    imported = await knowledge_service.import_employee_master_row(
        bid.org, batch, _master_row(2, "Engineer")
    )
    employee = imported.target_knowledge_item_id
    assert employee is not None
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(bid.tender, employee, True, bid.actor)
        await uow.commit()
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        knowledge_service.patch_employee_profile(
            employee, bid.org, bid.actor, current_title="Principal Engineer"
        )
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: knowledge_service.import_employee_master_row(
            bid.org, batch, _master_row(3, "Engineer")
        ),
        "the master row never waited for the profile edit",
    )
    _no_errors(one, two)
    _locked_read(seen, "employees")
    title = await _value(Employee.current_title, Employee.knowledge_item_id, employee)
    assert title == "Engineer"
    assert await _version(bid.tender) == start + 2


async def test_a_master_row_converging_onto_an_item_takes_its_before_under_the_lock(monkeypatch):
    """D2, the employee-master NEW branch. Its duplicate check found no one
    (a concurrent import created the employee after it looked), so the row
    converges onto the existing item. The sheet says "removed" (archived);
    a person restores the item meanwhile (active). Three steps: the row stops
    after it converged (B), the restore takes the item and stops (A), then
    the row goes on and must wait for the restore."""
    bid = await _bid()
    batch = await _import_batch(bid)
    imported = await knowledge_service.import_employee_master_row(
        bid.org, batch, _master_row(2, "Engineer", status="removed")
    )
    employee = imported.target_knowledge_item_id
    assert employee is not None
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(bid.tender, employee, True, bid.actor)
        await uow.commit()
    assert await _value(KnowledgeItem.status, KnowledgeItem.id, employee) == "archived"
    start = await _version(bid.tender)

    async def no_candidates(self, organization_id):
        return []

    monkeypatch.setattr(KnowledgeRepository, "find_employee_duplicate_candidates", no_candidates)
    row_stopped, row_go = pause_once(
        monkeypatch, knowledge_service, "_create_or_get_employee", after=True
    )
    restore_stopped, restore_go = pause_once(monkeypatch, AuditRepository, "create", after=False)
    row = asyncio.create_task(
        knowledge_service.import_employee_master_row(
            bid.org, batch, _master_row(3, "Engineer", status="removed")
        )
    )
    restore: asyncio.Task | None = None
    try:
        await asyncio.wait_for(row_stopped.wait(), timeout=10)
        restore = asyncio.create_task(
            knowledge_service.patch_knowledge_item(employee, bid.org, bid.actor, status="active")
        )
        await asyncio.wait_for(restore_stopped.wait(), timeout=10)
        row_go.set()
        await waiting(row, "the master row never waited for the restore")
    finally:
        row_go.set()
        restore_go.set()
        outcomes = await asyncio.gather(
            *(asyncio.wait_for(t, timeout=10) for t in (row, restore) if t is not None),
            return_exceptions=True,
        )
    _no_errors(*outcomes)
    assert await _value(KnowledgeItem.status, KnowledgeItem.id, employee) == "archived"
    assert await _version(bid.tender) == start + 2


async def _reviewed_requirement(bid: _Bid) -> uuid.UUID:
    """A mandatory experience requirement a person has already confirmed, so
    a second edit that sets its field back to the value it read has nothing
    else to write."""
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=bid.tender,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder shall have ten years of experience.",
            normalized_text="the bidder shall have ten years of experience.",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=bid.tender,
            display_code=f"EXP-{uuid.uuid4().hex[:4]}",
            requirement_text="Ten years of experience.",
            normalized_text="ten years of experience.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.requirements.update_fields(requirement.id, human_reviewed=True)
        await uow.commit()
        return requirement.id


@pytest.mark.parametrize(
    ("field", "changed", "back"),
    [("mandatory", False, True), ("category", "technical", "experience")],
)
async def test_two_requirement_edits_each_take_their_before_under_the_lock(
    monkeypatch, field, changed, back
):
    """D4, patch_requirement: a projected field changed (A) and set back (B).
    On main B wrote nothing at all, so it never waited: the requirement was
    already reviewed and its field read as the value B set."""
    bid = await _bid()
    requirement = await _reviewed_requirement(bid)
    start = await _version(bid.tender)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        requirement_service.patch_requirement(requirement, bid.org, bid.actor, **{field: changed})
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: requirement_service.patch_requirement(
            requirement, bid.org, bid.actor, **{field: back}
        ),
        "the second requirement edit never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "requirements")
    assert await _value(getattr(Requirement, field), Requirement.id, requirement) == back
    assert await _version(bid.tender) == start + 2
    befores = await _audit_befores(requirement, "requirement_edit")
    assert [b[field] for b in befores] == [back, changed]


async def _draft_section(bid: _Bid) -> uuid.UUID:
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(
            tender_id=bid.tender, section_name="Approach", content="First draft."
        )
        await uow.commit()
        return section.id


async def _two_section_edits(monkeypatch, bid: _Bid, section: uuid.UUID, b_text: str) -> list:
    """A edits the draft (it moves to review) and stops before its audit row;
    B edits it with b_text and must wait for A."""
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    first = asyncio.create_task(
        proposal_service.update_section_content(section, "A's text.", bid.actor)
    )
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: proposal_service.update_section_content(section, b_text, bid.actor),
        "the second section edit never waited for the first",
    )
    _no_errors(one, two)
    return seen


async def test_two_section_edits_each_take_their_before_under_the_lock(monkeypatch):
    """D5, update_section_content: two people edit one draft section. On main
    B's "before" was the draft A had already replaced, so its audit row hid
    A's edit, and it bumped again for the move to review A had made."""
    bid = await _bid()
    section = await _draft_section(bid)
    start = await _version(bid.tender)
    seen = await _two_section_edits(monkeypatch, bid, section, "B's text.")
    _locked_read(seen, "proposal_sections")
    assert await _value(ProposalSection.content, ProposalSection.id, section) == "B's text."
    assert await _version(bid.tender) == start + 1, "only A moved it to review"
    befores = await _audit_befores(section, "proposal_section_edit")
    assert befores == [
        {"content": "First draft.", "status": "draft"},
        {"content": "A's text.", "status": "review"},
    ]


async def test_a_section_edit_back_to_the_text_it_read_is_kept(monkeypatch):
    """D5: B saves the text it read, the first draft. On main B's UPDATE
    carried only the status (its content read as the value it set), so A's
    text stayed and B was answered with it."""
    bid = await _bid()
    section = await _draft_section(bid)
    seen = await _two_section_edits(monkeypatch, bid, section, "First draft.")
    _locked_read(seen, "proposal_sections")
    assert await _value(ProposalSection.content, ProposalSection.id, section) == "First draft."


async def _claim(bid: _Bid) -> uuid.UUID:
    async with UnitOfWork() as uow:
        section = await uow.proposal.create_proposal_section(
            tender_id=bid.tender, section_name="Team"
        )
        claim = await uow.proposal.create_proposal_claim(
            section_id=section.id,
            claim_text="Our lead has ten years.",
            claim_type="experience",
            is_factual=True,
            grounding_status="needs_review",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return claim.id


async def test_two_regroundings_each_take_their_before_under_the_lock(monkeypatch):
    """D6, reground_claim: two reanalysis jobs reground one claim. The first
    finds it unsupported (A); the second, after a later evidence change,
    finds it needs review again (B). The grounding function is patched to
    give those two answers in turn: it stands in for the evidence each job
    read. On main B read needs_review unlocked, wrote nothing and never
    waited, so A's unsupported stayed."""
    bid = await _bid()
    claim = await _claim(bid)
    answers = iter(["unsupported", "needs_review"])

    def grounding(*args, **kwargs) -> proposal_service.GroundingResult:
        return proposal_service.GroundingResult(status=next(answers), reason="test")

    monkeypatch.setattr(proposal_service, "enforce_claim_grounding", grounding)
    start = await _version(bid.tender)
    reached, release = asyncio.Event(), asyncio.Event()

    async def reground(hold: bool) -> None:
        async with UnitOfWork() as uow:
            await proposal_service.reground_claim(uow, claim)
            if hold:
                reached.set()
                await release.wait()
            await uow.commit()

    first = asyncio.create_task(reground(True))
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: reground(False),
        "the second regrounding never waited for the first",
    )
    _no_errors(one, two)
    _locked_read(seen, "proposal_claims")
    assert await _value(ProposalClaim.grounding_status, ProposalClaim.id, claim) == "needs_review"
    assert await _version(bid.tender) == start + 2


async def test_a_redraft_takes_an_existing_claims_before_under_the_lock(monkeypatch):
    """D7, persist_section_and_claims: a re-draft of a draft section finds
    its claim again by fingerprint and compares its grounding. A regrounding
    makes the claim unsupported and holds its transaction (A); the re-draft
    grounds it as needing review (B). The grounding function is patched to
    give the first draft's, A's and B's answers in turn. On main B read
    needs_review unlocked, wrote nothing and never waited, so A's
    unsupported stayed under B's re-draft."""
    bid = await _bid()
    answers = iter(["needs_review", "unsupported", "needs_review"])

    def grounding(*args, **kwargs) -> proposal_service.GroundingResult:
        return proposal_service.GroundingResult(status=next(answers), reason="test")

    monkeypatch.setattr(proposal_service, "enforce_claim_grounding", grounding)
    claim_draft = ProposalClaimDraft(claim_text="Our lead has ten years.", claim_type="experience")

    def section(content: str) -> ProposalSectionDraft:
        return ProposalSectionDraft(content=content, claims=[claim_draft])

    await proposal_service.persist_section_and_claims(bid.tender, "Team", section("One."), [], {})
    async with UnitOfWork() as uow:
        claim = (
            await uow.session.execute(
                select(ProposalClaim.id)
                .join(ProposalSection, ProposalSection.id == ProposalClaim.section_id)
                .where(ProposalSection.tender_id == bid.tender)
            )
        ).scalar_one()
    start = await _version(bid.tender)
    reached, release = asyncio.Event(), asyncio.Event()

    async def regrounding() -> None:
        async with UnitOfWork() as uow:
            await proposal_service.reground_claim(uow, claim)
            reached.set()
            await release.wait()
            await uow.commit()

    first = asyncio.create_task(regrounding())
    one, two, seen = await first_then_second(
        first,
        reached,
        release,
        lambda: proposal_service.persist_section_and_claims(
            bid.tender, "Team", section("Two."), [], {}
        ),
        "the re-draft never waited for the regrounding",
    )
    _no_errors(one, two)
    _locked_read(seen, "proposal_claims")
    assert await _value(ProposalClaim.grounding_status, ProposalClaim.id, claim) == "needs_review"
    assert await _version(bid.tender) == start + 2


async def test_an_evidence_link_to_an_item_does_not_wait_for_an_edit_of_it(monkeypatch):
    """The lock mode, not a race fix (it passes on main, where the edit took
    no read lock): the edit holds the certificate FOR NO KEY UPDATE, and a
    compliance evaluation on another bid links evidence to it meanwhile. Its
    foreign key check takes KEY SHARE, which FOR NO KEY UPDATE lets through
    and FOR UPDATE would not."""
    bid = await _bid()
    other = await _bid()
    cert = await _selected_certificate(bid)
    reached, release = pause_once(monkeypatch, AuditRepository, "create", after=False)
    edit = asyncio.create_task(
        knowledge_service.patch_knowledge_item(cert, bid.org, bid.actor, title="ISO/IEC 27001")
    )

    async def link() -> None:
        async with UnitOfWork() as uow:
            await uow.compliance.create_evidence_link(
                tender_id=other.tender,
                source_kind="certification",
                content_fingerprint=uuid.uuid4().hex,
                knowledge_item_id=cert,
            )
            await uow.commit()

    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        await finished_without_waiting(
            asyncio.create_task(link()), "an evidence link during an item edit"
        )
    finally:
        release.set()
        await asyncio.wait_for(edit, timeout=10)
    assert await _value(KnowledgeItem.title, KnowledgeItem.id, cert) == "ISO/IEC 27001"
