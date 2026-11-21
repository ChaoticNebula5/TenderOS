"""Plan item 112 (PR 25, user decisions 2026-11-20): approving an
employee-master review item applies its row to the employee through the
automatic branch's own apply (_apply_master_row_to_employee), including the
identity field that differed (Q5 b) and the sheet's code over a different one
(the user's addition); a master item naming no one gets plan item 111's pick
and import as new; approving a certification item creates its certificate
under a chosen holder through the automatic branch's own creation
(_create_certificate_from_row), writing the sheet's holder code onto a holder
who has none. After each decision, the same input imported again does not
come back to review, except rows with nothing to tell them apart.

Real Postgres. Self-built helpers, no imports from other test modules (this
suite's convention). Every fixture value is fake.
"""

import asyncio
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select, text
from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.knowledge import Employee
from tenderos_backend.db.repositories.knowledge import KnowledgeRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, knowledge_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio

NAME = "Rowan Vale"
TODAY = date(2026, 11, 20)


async def _org() -> tuple[uuid.UUID, uuid.UUID]:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Master And Certificate Org",
    )
    return registered.organization.id, registered.user.id


async def _employee(
    org_id: uuid.UUID,
    *,
    name: str = NAME,
    code: str | None = None,
    title: str | None = "Analyst",
    archived: bool = False,
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
            employee_code=code,
            current_title=title,
        )
        if archived:
            await uow.knowledge.update_knowledge_item(item.id, status="archived")
        await uow.commit()
    return item.id


async def _employee_row(employee_id: uuid.UUID) -> Employee:
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(employee_id)
    assert employee is not None
    return employee


async def _status(knowledge_item_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
    assert item is not None
    return item.status


async def _item(item_id: uuid.UUID):
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
    assert item is not None
    return item


async def _audits(action: str, target_id: uuid.UUID) -> list[AuditEvent]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(AuditEvent)
            .where(AuditEvent.action == action, AuditEvent.target_id == target_id)
            .order_by(AuditEvent.created_at)
        )
        return list(rows.scalars())


async def _employee_count(org_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return await uow.session.scalar(
            select(func.count()).select_from(Employee).where(Employee.organization_id == org_id)
        )


# ------------------------------------------------------------- the master


def _row(
    *, code: str | None = None, name: str = NAME, role: str | None = "Lead Analyst", status=None
) -> EmployeeMasterRow:
    return EmployeeMasterRow(row_number=2, employee_code=code, name=name, role=role, status=status)


async def _master_batch(org_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    batch = await knowledge_service.create_import_batch(
        org_id, "employee_master_xlsx", None, user_id
    )
    return batch.id


async def _import_master(org_id: uuid.UUID, user_id: uuid.UUID, row: EmployeeMasterRow):
    """The import's own path: a fresh batch (a re-upload makes one), the row."""
    batch_id = await _master_batch(org_id, user_id)
    item = await knowledge_service.import_employee_master_row(org_id, batch_id, row)
    return batch_id, item


async def _master_review(batch_id: uuid.UUID, item_id: uuid.UUID):
    targets, candidates = await knowledge_service.list_master_review(batch_id)
    return targets.get(item_id), candidates.get(item_id)


async def _approve_named(org_id, user_id, batch_id, item_id):
    target, _ = await _master_review(batch_id, item_id)
    assert target is not None
    return await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=target.fingerprint
    )


async def _pick_master(org_id, user_id, batch_id, item_id, employee_id):
    _, listed = await _master_review(batch_id, item_id)
    assert listed is not None
    chosen = next(c for c in listed.candidates if c.knowledge_item_id == employee_id)
    return await knowledge_service.approve_import_item(
        item_id,
        org_id,
        user_id,
        target_fingerprint=chosen.fingerprint,
        target_employee_id=employee_id,
        candidates_fingerprint=listed.fingerprint,
    )


async def test_approval_applies_the_row_onto_a_name_match_and_writes_the_sheets_code():
    """A name-only match (the employee has no code, the row has one) is in
    review naming them; approval writes the title, the status and the code
    (Q5 b), with an employee_master_apply row keeping the old values."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code=None)
    batch_id, item = await _import_master(
        org_id, user_id, _row(code="EMP-41", role="Principal Analyst", status="removed")
    )
    assert item.status == "needs_review" and item.target_knowledge_item_id == employee_id
    target, _ = await _master_review(batch_id, item.id)
    assert target is not None and target.changes is not None
    assert [(c.field, c.changes) for c in target.changes] == [
        ("name", False),
        ("employee_code", True),
        ("current_title", True),
        ("status", True),
    ]

    approved = await _approve_named(org_id, user_id, batch_id, item.id)

    employee = await _employee_row(employee_id)
    assert approved.status == "approved" and approved.target_knowledge_item_id == employee_id
    assert (employee.employee_code, employee.current_title) == ("EMP-41", "Principal Analyst")
    assert await _status(employee_id) == "archived"
    [apply_row] = await _audits("employee_master_apply", employee_id)
    assert apply_row.actor_type == "user" and apply_row.actor_id == str(user_id)
    assert apply_row.before["employee_code"] is None
    assert apply_row.after["employee_code"] == "EMP-41"
    assert apply_row.event_metadata["fields_changed"] == [
        "current_title",
        "employee_code",
        "status",
    ]
    [approve_row] = await _audits("knowledge_import_item_approve", item.id)
    assert approve_row.event_metadata["employee"] == str(employee_id)


async def test_approval_writes_the_sheets_name_when_the_code_matched():
    """Q5 b: the code matched one employee whose name differs; approval
    writes the sheet's name."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, name="Rowan Vael", code="EMP-42")
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-42"))
    assert item.status == "needs_review" and item.target_knowledge_item_id == employee_id

    await _approve_named(org_id, user_id, batch_id, item.id)

    assert (await _employee_row(employee_id)).name == NAME


async def test_approval_writes_the_sheets_code_over_a_different_one():
    """The user's addition: a name-matched row whose code differs from the
    one on file writes the sheet's code; the audit row keeps the old one."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code="EMP-OLD")
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-NEW"))
    assert item.status == "needs_review" and item.target_knowledge_item_id == employee_id

    await _approve_named(org_id, user_id, batch_id, item.id)

    assert (await _employee_row(employee_id)).employee_code == "EMP-NEW"
    [apply_row] = await _audits("employee_master_apply", employee_id)
    assert apply_row.before["employee_code"] == "EMP-OLD"


async def test_a_code_another_employee_holds_is_shown_kept_and_not_written():
    org_id, user_id = await _org()
    named = await _employee(org_id, code=None)
    await _employee(org_id, name="Someone Else", code="EMP-43")
    batch_id = await _master_batch(org_id, user_id)
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref="row_2",
            extracted_payload={"employee_code": "EMP-43", "name": NAME, "role": None},
        )
        await uow.knowledge.update_import_item(
            item.id, status="needs_review", target_knowledge_item_id=named
        )
        await uow.commit()
    target, _ = await _master_review(batch_id, item.id)
    assert target is not None and target.changes is not None
    code = next(c for c in target.changes if c.field == "employee_code")
    assert code.held_by_other and not code.changes

    await _approve_named(org_id, user_id, batch_id, item.id)

    assert (await _employee_row(named)).employee_code is None


async def test_an_archived_employee_is_applied_to_and_stays_archived():
    """Q6 (user decision 2026-11-20, parity): a row saying active restores no
    one; the listing says the employee is archived."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code=None, archived=True)
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-44", status="active"))
    target, _ = await _master_review(batch_id, item.id)
    assert target is not None and target.archived

    await _approve_named(org_id, user_id, batch_id, item.id)

    assert await _status(employee_id) == "archived"
    assert (await _employee_row(employee_id)).current_title == "Lead Analyst"


async def test_a_master_approval_needs_the_fingerprint_shown():
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code=None)
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-45"))
    with pytest.raises(knowledge_service.ImportItemFingerprintRequiredError):
        await knowledge_service.approve_import_item(item.id, org_id, user_id)
    target, _ = await _master_review(batch_id, item.id)
    assert target is not None
    async with UnitOfWork() as uow:
        await uow.knowledge.update_employee(employee_id, current_title="Changed")
        await uow.commit()
    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint=target.fingerprint
        )
    assert (await _item(item.id)).status == "needs_review"
    assert (await _employee_row(employee_id)).employee_code is None


async def test_a_master_item_that_does_not_read_as_a_row_is_refused():
    org_id, user_id = await _org()
    employee_id = await _employee(org_id)
    batch_id = await _master_batch(org_id, user_id)
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id, source_ref="row_2", extracted_payload={"name": "  "}
        )
        await uow.knowledge.update_import_item(
            item.id, status="needs_review", target_knowledge_item_id=employee_id
        )
        await uow.commit()
    target, candidates = await _master_review(batch_id, item.id)
    assert target is not None and target.changes is None and candidates is None
    with pytest.raises(knowledge_service.ImportItemPayloadInvalidError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint=target.fingerprint
        )


async def test_a_master_item_naming_no_one_is_refused_without_a_pick():
    org_id, user_id = await _org()
    await _employee(org_id, title="Analyst")
    await _employee(org_id, title="Engineer")
    batch_id, item = await _import_master(org_id, user_id, _row())
    assert item.status == "needs_review" and item.target_knowledge_item_id is None
    with pytest.raises(knowledge_service.ImportItemNoTargetError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint="x"
        )


async def test_the_automatic_branch_writes_the_same_audit_row_with_a_system_actor():
    """Q8: the automatic DUPLICATE branch (code and name match) applies
    through the same function and writes employee_master_apply too."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code="EMP-46")
    _, item = await _import_master(org_id, user_id, _row(code="EMP-46", role="Director"))
    assert item.status == "approved"
    [apply_row] = await _audits("employee_master_apply", employee_id)
    assert (apply_row.actor_type, apply_row.actor_id) == ("system", "employee_master_import")
    assert apply_row.event_metadata == {
        "import_item_id": str(item.id),
        "fields_changed": ["current_title"],
    }
    assert (await _employee_row(employee_id)).current_title == "Director"


async def test_approval_and_the_automatic_branch_end_with_the_same_employee():
    """One apply: the same row, automatically onto a code-and-name match in
    one organization and by approval onto a name match in another, leaves
    the same values."""
    row = _row(code="EMP-47", role="Head of Delivery", status="removed")
    auto_org, auto_user = await _org()
    auto_id = await _employee(auto_org, code="EMP-47")
    _, auto_item = await _import_master(auto_org, auto_user, row)
    review_org, review_user = await _org()
    review_id = await _employee(review_org, code=None)
    batch_id, review_item = await _import_master(review_org, review_user, row)
    await _approve_named(review_org, review_user, batch_id, review_item.id)

    assert auto_item.status == "approved"
    [auto_row] = await _audits("employee_master_apply", auto_id)
    [review_row] = await _audits("employee_master_apply", review_id)
    assert (
        auto_row.after
        == review_row.after
        == {
            "name": NAME,
            "employee_code": "EMP-47",
            "current_title": "Head of Delivery",
            "status": "archived",
        }
    )


async def test_a_name_change_bumps_the_bids_and_a_code_only_change_does_not():
    org_id, user_id = await _org()
    renamed = await _employee(org_id, name="Rowan Vael", code="EMP-48", title="Lead Analyst")
    recoded = await _employee(org_id, name="Ada Quill", code=None, title="Lead Analyst")
    tender = await create_tender(organization_id=org_id, name="Bid", created_by=user_id)
    async with UnitOfWork() as uow:
        await uow.knowledge.upsert_selection(tender.id, renamed, True, user_id)
        await uow.knowledge.upsert_selection(tender.id, recoded, True, user_id)
        await uow.commit()

    async def version() -> int:
        async with UnitOfWork() as uow:
            current = await uow.tenders.get(tender.id)
        assert current is not None
        return current.state_version

    start = await version()
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-49", name="Ada Quill"))
    await _approve_named(org_id, user_id, batch_id, item.id)
    assert await version() == start
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-48"))
    await _approve_named(org_id, user_id, batch_id, item.id)
    assert await version() == start + 1


async def test_a_master_pick_applies_the_row_to_the_chosen_candidate():
    org_id, user_id = await _org()
    await _employee(org_id, title="Analyst")
    second = await _employee(org_id, title="Engineer")
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-50"))
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None and len(listed.candidates) == 2 and not listed.strong_match

    approved = await _pick_master(org_id, user_id, batch_id, item.id, second)

    assert approved.target_knowledge_item_id == second
    assert (await _employee_row(second)).employee_code == "EMP-50"
    [approve_row] = await _audits("knowledge_import_item_approve", item.id)
    assert approve_row.event_metadata["decision"] == "pick"


async def test_a_master_pick_refuses_a_changed_list_and_a_non_candidate():
    org_id, user_id = await _org()
    first = await _employee(org_id, title="Analyst")
    await _employee(org_id, title="Engineer")
    stranger = await _employee(org_id, name="Someone Else")
    batch_id, item = await _import_master(org_id, user_id, _row())
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None
    with pytest.raises(knowledge_service.ImportItemNotACandidateError):
        await knowledge_service.approve_import_item(
            item.id,
            org_id,
            user_id,
            target_fingerprint="x",
            target_employee_id=stranger,
            candidates_fingerprint=listed.fingerprint,
        )
    await _employee(org_id, title="Third")
    with pytest.raises(knowledge_service.ImportItemCandidatesChangedError):
        await knowledge_service.approve_import_item(
            item.id,
            org_id,
            user_id,
            target_fingerprint=listed.candidates[0].fingerprint,
            target_employee_id=first,
            candidates_fingerprint=listed.fingerprint,
        )


async def test_import_as_new_makes_the_employee_the_new_branch_would():
    """The same row, through the automatic NEW branch in one organization and
    import as new in another (after a name-only match), makes the same
    employee: one definition."""
    row = _row(code="EMP-51", role="Surveyor")
    new_org, new_user = await _org()
    _, new_item = await _import_master(new_org, new_user, row)
    assert new_item.status == "approved"
    review_org, review_user = await _org()
    await _employee(review_org, code=None)
    await _employee(review_org, code=None, title="Engineer")
    batch_id, item = await _import_master(review_org, review_user, row)
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None and not listed.strong_match
    before = await _employee_count(review_org)

    updated = await knowledge_service.import_item_as_new_employee(
        item.id, review_org, review_user, listed.fingerprint
    )

    assert await _employee_count(review_org) == before + 1
    made = await _employee_row(updated.target_knowledge_item_id)
    auto = await _employee_row(new_item.target_knowledge_item_id)
    assert (made.name, made.employee_code, made.current_title) == (
        auto.name,
        auto.employee_code,
        auto.current_title,
    )
    async with UnitOfWork() as uow:
        made_item = await uow.knowledge.get_knowledge_item(made.knowledge_item_id)
    assert made_item is not None
    assert made_item.content_fingerprint == knowledge_service.master_employee_fingerprint(
        review_org, row
    )
    replay = await knowledge_service.import_item_as_new_employee(
        item.id, review_org, review_user, listed.fingerprint
    )
    assert replay.target_knowledge_item_id == updated.target_knowledge_item_id
    assert await _employee_count(review_org) == before + 1


async def test_import_as_new_is_refused_on_a_code_match_or_an_existing_fingerprint():
    org_id, user_id = await _org()
    await _employee(org_id, name="Rowan Vael", code="EMP-52")
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-52"))
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None and listed.strong_match
    with pytest.raises(knowledge_service.ImportItemStrongMatchError):
        await knowledge_service.import_item_as_new_employee(
            item.id, org_id, user_id, listed.fingerprint
        )

    # A row with no code, whose NEW fingerprint an earlier master row made.
    other_org, other_user = await _org()
    _, first = await _import_master(other_org, other_user, _row(code=None))
    assert first.status == "approved"
    await _employee(other_org, code=None, title="Engineer")
    batch_id, item = await _import_master(other_org, other_user, _row(code=None))
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None and listed.strong_match
    with pytest.raises(knowledge_service.ImportItemStrongMatchError):
        await knowledge_service.import_item_as_new_employee(
            item.id, other_org, other_user, listed.fingerprint
        )


# ---------------------------------------------- the master, imported again


async def test_after_each_master_decision_the_same_row_imports_without_review():
    """The re-import check: a re-upload makes a new batch, so every row is
    imported again. After approving a code match whose name differed, a name
    match with no code on file, a name match with a different code, a pick
    and an import as new, the same row is a duplicate and applies itself."""
    org_id, user_id = await _org()

    named_by_code = await _employee(org_id, name="Rowan Vael", code="EMP-60")
    row = _row(code="EMP-60")
    batch_id, item = await _import_master(org_id, user_id, row)
    await _approve_named(org_id, user_id, batch_id, item.id)
    _, again = await _import_master(org_id, user_id, row)
    assert (again.status, again.target_knowledge_item_id) == ("approved", named_by_code)

    for name, code_on_file, code in (
        ("Ada Quill", None, "EMP-61"),
        ("Basil Fenn", "EMP-OLD-62", "EMP-62"),
    ):
        employee_id = await _employee(org_id, name=name, code=code_on_file)
        row = _row(name=name, code=code)
        batch_id, item = await _import_master(org_id, user_id, row)
        assert item.status == "needs_review"
        await _approve_named(org_id, user_id, batch_id, item.id)
        _, again = await _import_master(org_id, user_id, row)
        assert (again.status, again.target_knowledge_item_id) == ("approved", employee_id)

    await _employee(org_id, name="Cora Lind", title="Analyst")
    chosen = await _employee(org_id, name="Cora Lind", title="Engineer")
    row = _row(name="Cora Lind", code="EMP-63")
    batch_id, item = await _import_master(org_id, user_id, row)
    await _pick_master(org_id, user_id, batch_id, item.id, chosen)
    _, again = await _import_master(org_id, user_id, row)
    assert (again.status, again.target_knowledge_item_id) == ("approved", chosen)

    await _employee(org_id, name="Dev Rao")
    row = _row(name="Dev Rao", code="EMP-64")
    batch_id, item = await _import_master(org_id, user_id, row)
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None
    made = await knowledge_service.import_item_as_new_employee(
        item.id, org_id, user_id, listed.fingerprint
    )
    _, again = await _import_master(org_id, user_id, row)
    assert (again.status, again.target_knowledge_item_id) == (
        "approved",
        made.target_knowledge_item_id,
    )


async def test_a_row_with_nothing_to_tell_it_apart_comes_back_to_review():
    """The exception (plan item 112's note): a row with no code, approved
    once, is a name-only match again on the next upload."""
    org_id, user_id = await _org()
    await _employee(org_id, code=None)
    batch_id, item = await _import_master(org_id, user_id, _row(code=None))
    await _approve_named(org_id, user_id, batch_id, item.id)
    _, again = await _import_master(org_id, user_id, _row(code=None))
    assert again.status == "needs_review"


# --------------------------------------------------------- certifications


async def _sheet(org_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    label = uuid.uuid4().hex
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=f"certifications_{label}.xlsx",
        content=f"placeholder:{label}".encode(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        side="company",
        doc_type="certification_documents",
    )
    return document.id


def _cert_row(ref: str | None = "EMP-70", name: str = "PMP") -> CertificationRow:
    return CertificationRow(
        row_number=2,
        cert_key="C-1",
        employee_ref=ref,
        name=name,
        issuer="PMI",
        issue_date=date(2025, 1, 1),
        expiry_date=date(2028, 1, 1),
    )


async def _import_cert(org_id, user_id, sheet_id, row):
    batch = await knowledge_service.create_import_batch(
        org_id, "certification_documents", sheet_id, user_id
    )
    item = await knowledge_service.import_certification_row(org_id, batch.id, sheet_id, row, TODAY)
    return batch.id, item


async def _holder(batch_id: uuid.UUID, employee_id: uuid.UUID):
    holders = await knowledge_service.list_certificate_holders(batch_id)
    assert holders is not None
    return next(h for h in holders if h.knowledge_item_id == employee_id)


async def _give(org_id, user_id, batch_id, item_id, employee_id):
    holder = await _holder(batch_id, employee_id)
    return await knowledge_service.approve_import_item(
        item_id,
        org_id,
        user_id,
        target_fingerprint=holder.fingerprint,
        target_employee_id=employee_id,
    )


async def _certificate_facts(knowledge_item_id: uuid.UUID) -> dict[str, object]:
    """What a certificate holds, from its knowledge item (dates, status,
    source) and its certification row (holder, issuer), with its chunk's
    text."""
    async with UnitOfWork() as uow:
        knowledge_item = await uow.knowledge.get_knowledge_item(knowledge_item_id)
        certification = await uow.knowledge.get_certification(knowledge_item_id)
        assert knowledge_item is not None and certification is not None
        assert knowledge_item.source_chunk_id is not None
        chunk = await uow.documents.get_chunk(knowledge_item.source_chunk_id)
    assert chunk is not None
    return {
        "holder": certification.holder_employee_item_id,
        "issuer": certification.issuer,
        "valid_from": knowledge_item.valid_from,
        "valid_until": knowledge_item.valid_until,
        "status": knowledge_item.status,
        "source_document_id": knowledge_item.source_document_id,
        "chunk_text": chunk.chunk_text,
    }


async def _certification_count(org_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        rows = await uow.knowledge.list_knowledge_items_for_org(org_id, "certification")
    return len(rows)


async def test_approval_creates_the_certificate_under_the_chosen_holder_and_writes_the_code():
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-70"))
    assert item.status == "needs_review" and item.target_knowledge_item_id is None

    approved = await _give(org_id, user_id, batch_id, item.id, holder_id)

    assert await _certificate_facts(approved.target_knowledge_item_id) == {
        "holder": holder_id,
        "issuer": "PMI",
        "valid_from": date(2025, 1, 1),
        "valid_until": date(2028, 1, 1),
        "status": "active",
        "source_document_id": sheet_id,
        "chunk_text": "PMP, issued by PMI, valid from 2025-01-01 to 2028-01-01.",
    }
    assert (await _employee_row(holder_id)).employee_code == "EMP-70"
    [approve_row] = await _audits("knowledge_import_item_approve", item.id)
    assert approve_row.event_metadata == {
        "certification": str(approved.target_knowledge_item_id),
        "holder": str(holder_id),
        "created": True,
        "sheet_holder_code": "EMP-70",
        "holder_code": None,
        "holder_code_written": True,
    }


async def test_a_holder_with_a_different_code_keeps_it_and_the_audit_records_both():
    """PR 25's review: a sheet holder code that differs from the chosen
    holder's own is not written over it (the certification sheet is not the
    HR record); the approval's audit row records both codes."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code="EMP-OWN-90")
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-90"))

    await _give(org_id, user_id, batch_id, item.id, holder_id)

    assert (await _employee_row(holder_id)).employee_code == "EMP-OWN-90"
    [approve_row] = await _audits("knowledge_import_item_approve", item.id)
    assert approve_row.event_metadata["sheet_holder_code"] == "EMP-90"
    assert approve_row.event_metadata["holder_code"] == "EMP-OWN-90"
    assert approve_row.event_metadata["holder_code_written"] is False


async def test_a_holder_with_a_code_keeps_it_and_a_blank_cell_writes_none():
    org_id, user_id = await _org()
    coded = await _employee(org_id, code="EMP-OWN")
    uncoded = await _employee(org_id, name="Ada Quill", code=None)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-71"))
    await _give(org_id, user_id, batch_id, item.id, coded)
    assert (await _employee_row(coded)).employee_code == "EMP-OWN"
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row(None, "ISO 9001"))
    await _give(org_id, user_id, batch_id, item.id, uncoded)
    assert (await _employee_row(uncoded)).employee_code is None


async def test_the_automatic_branch_and_approval_make_the_same_certificate():
    """One creation: a row whose holder code is found, and the same row
    approved for that holder after its code was not found, make certificates
    with the same fingerprint, dates, status and chunk text."""
    row = _cert_row("EMP-72")
    auto_org, auto_user = await _org()
    await _employee(auto_org, code="EMP-72")
    auto_sheet = await _sheet(auto_org, auto_user)
    _, auto_item = await _import_cert(auto_org, auto_user, auto_sheet, row)
    review_org, review_user = await _org()
    holder_id = await _employee(review_org, code=None)
    review_sheet = await _sheet(review_org, review_user)
    batch_id, item = await _import_cert(review_org, review_user, review_sheet, row)
    approved = await _give(review_org, review_user, batch_id, item.id, holder_id)

    auto = await _certificate_facts(auto_item.target_knowledge_item_id)
    review = await _certificate_facts(approved.target_knowledge_item_id)
    differs = {"holder", "source_document_id"}  # two organizations, two sheets
    assert {k: v for k, v in auto.items() if k not in differs} == {
        k: v for k, v in review.items() if k not in differs
    }
    async with UnitOfWork() as uow:
        auto_item_row = await uow.knowledge.get_knowledge_item(auto_item.target_knowledge_item_id)
        review_item_row = await uow.knowledge.get_knowledge_item(approved.target_knowledge_item_id)
    assert auto_item_row is not None and review_item_row is not None
    assert auto_item_row.content_fingerprint == knowledge_service.certification_fingerprint(
        auto_org, auto["holder"], row
    )
    assert review_item_row.content_fingerprint == knowledge_service.certification_fingerprint(
        review_org, holder_id, row
    )


async def test_an_existing_certificate_is_approved_onto_not_duplicated():
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code="EMP-73")
    sheet_id = await _sheet(org_id, user_id)
    _, auto_item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-73"))
    before = await _certification_count(org_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-MISSING"))

    approved = await _give(org_id, user_id, batch_id, item.id, holder_id)

    assert approved.target_knowledge_item_id == auto_item.target_knowledge_item_id
    assert await _certification_count(org_id) == before
    [approve_row] = await _audits("knowledge_import_item_approve", item.id)
    assert approve_row.event_metadata["created"] is False


async def test_a_certificate_won_by_a_concurrent_row_is_converged_on_in_the_savepoint(
    monkeypatch,
):
    """The insert runs in a savepoint: when the certificate appears between
    the fingerprint read and the insert (a concurrent identical row), the
    insert fails, the savepoint rolls back, and the approval returns the
    certificate that won, in a transaction that still commits."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code="EMP-74")
    sheet_id = await _sheet(org_id, user_id)
    _, auto_item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-74"))
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-NONE"))
    holder = await _holder(batch_id, holder_id)
    real = KnowledgeRepository.get_knowledge_item_by_fingerprint
    calls = {"certification": 0}

    async def first_miss(self, organization_id, item_type, fingerprint):
        if item_type == "certification":
            calls["certification"] += 1
            if calls["certification"] == 1:
                return None
        return await real(self, organization_id, item_type, fingerprint)

    monkeypatch.setattr(KnowledgeRepository, "get_knowledge_item_by_fingerprint", first_miss)
    approved = await knowledge_service.approve_import_item(
        item.id,
        org_id,
        user_id,
        target_fingerprint=holder.fingerprint,
        target_employee_id=holder_id,
    )
    monkeypatch.undo()

    assert calls["certification"] == 2
    assert approved.status == "approved"
    assert approved.target_knowledge_item_id == auto_item.target_knowledge_item_id
    assert (await _item(item.id)).status == "approved"


async def test_a_certificate_approval_refuses_what_it_cannot_record():
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    archived_id = await _employee(org_id, name="Gone Person", archived=True)
    other_org, _ = await _org()
    outsider = await _employee(other_org)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-75"))
    holder = await _holder(batch_id, holder_id)

    with pytest.raises(knowledge_service.ImportItemNoTargetError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint=holder.fingerprint
        )
    with pytest.raises(knowledge_service.ImportItemFingerprintRequiredError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_employee_id=holder_id
        )
    with pytest.raises(knowledge_service.ImportItemNotACandidateError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint="x", target_employee_id=outsider
        )
    with pytest.raises(knowledge_service.EmployeeArchivedError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint="x", target_employee_id=archived_id
        )
    async with UnitOfWork() as uow:
        await uow.knowledge.update_employee(holder_id, name="Renamed")
        await uow.commit()
    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await knowledge_service.approve_import_item(
            item.id,
            org_id,
            user_id,
            target_fingerprint=holder.fingerprint,
            target_employee_id=holder_id,
        )
    holder = await _holder(batch_id, holder_id)
    async with UnitOfWork() as uow:
        await uow.session.execute(
            text("UPDATE knowledge_import_batches SET source_document_id = NULL WHERE id = :id"),
            {"id": batch_id},
        )
        await uow.commit()
    with pytest.raises(knowledge_service.ImportItemSourceMissingError):
        await knowledge_service.approve_import_item(
            item.id,
            org_id,
            user_id,
            target_fingerprint=holder.fingerprint,
            target_employee_id=holder_id,
        )
    assert (await _item(item.id)).status == "needs_review"
    assert (await _employee_row(holder_id)).employee_code is None


async def test_a_certificate_item_that_does_not_read_as_a_row_is_refused():
    org_id, user_id = await _org()
    holder_id = await _employee(org_id)
    sheet_id = await _sheet(org_id, user_id)
    batch = await knowledge_service.create_import_batch(
        org_id, "certification_documents", sheet_id, user_id
    )
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch.id,
            source_ref="row_2",
            extracted_payload={"name": "PMP", "issue_date": "not a date"},
        )
        await uow.knowledge.update_import_item(item.id, status="needs_review")
        await uow.commit()
    holder = await _holder(batch.id, holder_id)
    with pytest.raises(knowledge_service.ImportItemPayloadInvalidError):
        await knowledge_service.approve_import_item(
            item.id,
            org_id,
            user_id,
            target_fingerprint=holder.fingerprint,
            target_employee_id=holder_id,
        )


async def test_a_certificate_replay_is_the_same_holder_only():
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    other = await _employee(org_id, name="Ada Quill")
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-76"))
    approved = await _give(org_id, user_id, batch_id, item.id, holder_id)
    replay = await knowledge_service.approve_import_item(
        item.id, org_id, user_id, target_fingerprint="x", target_employee_id=holder_id
    )
    assert replay.target_knowledge_item_id == approved.target_knowledge_item_id
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await knowledge_service.approve_import_item(
            item.id, org_id, user_id, target_fingerprint="x", target_employee_id=other
        )


async def test_the_holder_choice_lists_live_employees_only():
    org_id, user_id = await _org()
    live = await _employee(org_id)
    archived = await _employee(org_id, name="Gone Person", archived=True)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, _ = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-77"))
    holders = await knowledge_service.list_certificate_holders(batch_id)
    assert holders is not None
    ids = [h.knowledge_item_id for h in holders]
    assert live in ids and archived not in ids
    master_batch = await _master_batch(org_id, user_id)
    assert await knowledge_service.list_certificate_holders(master_batch) == []
    assert await knowledge_service.list_certificate_holders(uuid.uuid4()) is None


async def test_after_a_holder_is_chosen_the_same_row_imports_without_review():
    """The re-import check for certifications: the sheet's code, written onto
    a holder who had none, finds them next time, and the certificate's
    fingerprint approves the row onto the same certificate."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-78"))
    approved = await _give(org_id, user_id, batch_id, item.id, holder_id)
    count = await _certification_count(org_id)

    _, again = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-78"))

    assert again.status == "approved"
    assert again.target_knowledge_item_id == approved.target_knowledge_item_id
    assert await _certification_count(org_id) == count


async def test_certificate_rows_that_cannot_find_their_holder_come_back_to_review():
    """The exceptions: a blank holder cell has nothing to tell it apart (plan
    items 115 and 112's note), and a sheet code that differs from the chosen
    holder's own code is not written (the master is the HR record, not the
    certification sheet), so both are in review again; approving again
    converges on the same certificate."""
    org_id, user_id = await _org()
    coded = await _employee(org_id, code="EMP-OWN-79")
    uncoded = await _employee(org_id, name="Ada Quill", code=None)
    sheet_id = await _sheet(org_id, user_id)
    for holder_id, row in ((coded, _cert_row("EMP-79")), (uncoded, _cert_row(None, "ISO"))):
        batch_id, item = await _import_cert(org_id, user_id, sheet_id, row)
        approved = await _give(org_id, user_id, batch_id, item.id, holder_id)
        batch_id, again = await _import_cert(org_id, user_id, sheet_id, row)
        assert again.status == "needs_review"
        second = await _give(org_id, user_id, batch_id, again.id, holder_id)
        assert second.target_knowledge_item_id == approved.target_knowledge_item_id


# --------------------------------------------------------------- lock order


async def test_a_master_approval_refuses_a_pending_item_without_waiting_for_the_employee_lock():
    """As PR 18: the import's automatic branch locks the employee and then
    its own item, still pending; approval refuses a pending item before it
    asks for the employee lock, so the two never wait in a circle."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code=None)
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-80"))
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item.id, status="pending")
        await uow.commit()

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": employee_id},
        )
        with pytest.raises(knowledge_service.ImportItemNotInReviewError):
            await asyncio.wait_for(
                knowledge_service.approve_import_item(
                    item.id, org_id, user_id, target_fingerprint="x"
                ),
                timeout=10,
            )
        await holder.rollback()


async def test_a_master_approval_waits_for_the_employee_then_sees_its_values():
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, code=None)
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-81"))
    target, _ = await _master_review(batch_id, item.id)
    assert target is not None

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": employee_id},
        )
        approval = asyncio.create_task(
            knowledge_service.approve_import_item(
                item.id, org_id, user_id, target_fingerprint=target.fingerprint
            )
        )
        await asyncio.sleep(1.0)
        assert not approval.done(), "the approval waits for the employee's row"
        await holder.session.execute(
            text("UPDATE employees SET current_title = 'Moved' WHERE knowledge_item_id = :id"),
            {"id": employee_id},
        )
        await holder.commit()

    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await asyncio.wait_for(approval, timeout=10)
    assert (await _item(item.id)).status == "needs_review"


async def test_master_import_as_new_takes_no_employee_lock():
    org_id, user_id = await _org()
    first = await _employee(org_id, code=None)
    await _employee(org_id, code=None, title="Engineer")
    batch_id, item = await _import_master(org_id, user_id, _row(code="EMP-82"))
    _, listed = await _master_review(batch_id, item.id)
    assert listed is not None

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": first},
        )
        updated = await asyncio.wait_for(
            knowledge_service.import_item_as_new_employee(
                item.id, org_id, user_id, listed.fingerprint
            ),
            timeout=10,
        )
        await holder.rollback()
    assert updated.status == "approved"


async def _approve_cert_task(org_id, user_id, batch_id, item_id, holder_id):
    holder = await _holder(batch_id, holder_id)
    return asyncio.create_task(
        knowledge_service.approve_import_item(
            item_id,
            org_id,
            user_id,
            target_fingerprint=holder.fingerprint,
            target_employee_id=holder_id,
        )
    )


async def test_a_certificate_approval_locks_the_holder_for_no_key_update():
    """The holder's row is read in the mode its code write takes (FOR NO KEY
    UPDATE), not FOR SHARE then a write (PR 20's lock-upgrade trap): a
    transaction holding the row FOR SHARE is waited for, and one holding it
    FOR KEY SHARE (what a foreign key check takes) is not."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)

    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-83"))
    async with UnitOfWork() as other:
        await other.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR SHARE"),
            {"id": holder_id},
        )
        approval = await _approve_cert_task(org_id, user_id, batch_id, item.id, holder_id)
        await asyncio.sleep(1.0)
        assert not approval.done(), "FOR SHARE blocks FOR NO KEY UPDATE"
        await other.rollback()
    approved = await asyncio.wait_for(approval, timeout=60)
    assert approved.status == "approved" and approved.target_knowledge_item_id is not None

    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row(None, "ISO"))
    async with UnitOfWork() as other:
        await other.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR KEY SHARE"),
            {"id": holder_id},
        )
        approval = await _approve_cert_task(org_id, user_id, batch_id, item.id, holder_id)
        approved = await asyncio.wait_for(approval, timeout=60)
        await other.rollback()
    assert approved.status == "approved" and approved.target_knowledge_item_id is not None


async def test_a_certificate_approval_refuses_a_pending_item_without_waiting_for_the_holder():
    """The item first, then the holder: a pending item is refused before
    the holder's row is asked for."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-84"))
    holder = await _holder(batch_id, holder_id)
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item.id, status="pending")
        await uow.commit()

    async with UnitOfWork() as other:
        await other.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": holder_id},
        )
        with pytest.raises(knowledge_service.ImportItemNotInReviewError):
            await asyncio.wait_for(
                knowledge_service.approve_import_item(
                    item.id,
                    org_id,
                    user_id,
                    target_fingerprint=holder.fingerprint,
                    target_employee_id=holder_id,
                ),
                timeout=10,
            )
        await other.rollback()


async def test_a_certificate_approval_waits_for_the_holder_then_sees_its_values():
    """The holder's row is locked when it is read, not only when the code is
    written: on a blank-holder row (no code to write) a transaction holding
    the holder is still waited for, and the approval then compares the
    fingerprint with the values that transaction left."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)
    batch_id, item = await _import_cert(org_id, user_id, sheet_id, _cert_row(None, "ISO"))

    async with UnitOfWork() as other:
        await other.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": holder_id},
        )
        approval = await _approve_cert_task(org_id, user_id, batch_id, item.id, holder_id)
        await asyncio.sleep(1.0)
        assert not approval.done(), "the approval waits for the holder's row"
        await other.session.execute(
            text("UPDATE employees SET name = 'Renamed' WHERE knowledge_item_id = :id"),
            {"id": holder_id},
        )
        await other.commit()

    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await asyncio.wait_for(approval, timeout=10)
    assert (await _item(item.id)).status == "needs_review"


async def test_two_certificate_approvals_for_one_holder_do_not_deadlock():
    """PR 20's lock-upgrade trap: two transactions that each read the holder
    FOR SHARE and then write its code would each wait for the other's share
    lock, and Postgres would abort one. Read FOR NO KEY UPDATE, the second
    waits for the first, then finds the code written and writes nothing.
    Both are held behind a FOR SHARE until both have started."""
    org_id, user_id = await _org()
    holder_id = await _employee(org_id, code=None)
    sheet_id = await _sheet(org_id, user_id)
    first_batch, first = await _import_cert(org_id, user_id, sheet_id, _cert_row("EMP-85"))
    second_batch, second = await _import_cert(
        org_id, user_id, sheet_id, _cert_row("EMP-85", "ISO 27001")
    )

    async with UnitOfWork() as other:
        await other.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR SHARE"),
            {"id": holder_id},
        )
        approvals = [
            await _approve_cert_task(org_id, user_id, first_batch, first.id, holder_id),
            await _approve_cert_task(org_id, user_id, second_batch, second.id, holder_id),
        ]
        await asyncio.sleep(1.0)
        assert not any(a.done() for a in approvals)
        await other.rollback()

    results = await asyncio.wait_for(asyncio.gather(*approvals, return_exceptions=True), timeout=60)
    # The first to take the holder writes the code. The second then either
    # finds the holder it was shown (approved) or a holder whose code
    # changed (a clean 409 TARGET_CHANGED); never a deadlock.
    approved = [r for r in results if not isinstance(r, BaseException)]
    refused = [r for r in results if isinstance(r, BaseException)]
    assert approved and all(r.status == "approved" for r in approved)
    assert all(isinstance(r, knowledge_service.ImportItemTargetChangedError) for r in refused), (
        refused
    )
    assert (await _employee_row(holder_id)).employee_code == "EMP-85"
