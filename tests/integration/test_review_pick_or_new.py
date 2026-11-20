"""Plan item 111 (PR 24, user decisions 2026-11-20): a resume review item can
be merged into an employee the reviewer picks from its candidates (the
duplicate check's own matches), or imported as a new person, which is refused
when a strong signal matched. Plus plan item 112's note 1 (strings trimmed,
a value of only spaces blank) and the re-import fix (the merge keeps an email
another employee holds).

Real Postgres. Self-built helpers, no imports from other test modules (this
suite's convention). Every fixture value is fake.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select, text
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.knowledge import Employee, KnowledgeItemChunk
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_RESUME_MERGE_APPROVED
from tenderos_backend.domain.services import document_service, knowledge_service
from tenderos_backend.domain.services.auth_service import register

pytestmark = pytest.mark.asyncio

NAME = "Priya Shah"


def _profile(**overrides: object) -> ExtractedEmployeeProfile:
    fields: dict[str, object] = {
        "name": NAME,
        "email": None,
        "current_title": "Lead Analyst",
        "professional_location": "Pune",
        "years_experience": 9.0,
        "confidence": 0.9,
    }
    fields.update(overrides)
    return ExtractedEmployeeProfile.model_validate(fields)


async def _org() -> tuple[uuid.UUID, uuid.UUID]:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Pick Or New Org",
    )
    return registered.organization.id, registered.user.id


async def _document(org_id: uuid.UUID, user_id: uuid.UUID) -> tuple[uuid.UUID, list[uuid.UUID]]:
    label = uuid.uuid4().hex
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=f"resume_{label}.docx",
        content=f"placeholder:{label}".encode(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="resume",
    )
    chunk_ids = []
    async with UnitOfWork() as uow:
        for i, chunk_text in enumerate(["Lead Analyst, 9 years.", "Pune office."]):
            chunk = await uow.documents.create_chunk(
                document_id=document.id,
                chunk_index=i,
                chunk_text=chunk_text,
                normalized_text=chunk_text.lower(),
                content_hash=uuid.uuid4().hex,
            )
            chunk_ids.append(chunk.id)
        await uow.commit()
    return document.id, chunk_ids


async def _employee(
    org_id: uuid.UUID,
    *,
    name: str = NAME,
    email: str | None = None,
    current_title: str | None = "Analyst",
    resume_document_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=org_id,
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
            email=email,
            current_title=current_title,
            resume_document_id=resume_document_id,
        )
        await uow.commit()
    return item.id


async def _review_item(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    profile: ExtractedEmployeeProfile,
    *,
    target_id: uuid.UUID | None = None,
    document: tuple[uuid.UUID, list[uuid.UUID]] | None = None,
    import_type: str = "bulk_resumes",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """(batch, item, document, chunk ids): a resume item in review."""
    document_id, chunk_ids = document or await _document(org_id, user_id)
    batch = await knowledge_service.create_import_batch(org_id, import_type, None, user_id)
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch.id,
            source_ref=f"{document_id}/resume.docx",
            extracted_payload=profile.model_dump(),
            source_document_id=document_id,
        )
        await uow.knowledge.update_import_item(
            item.id,
            status="needs_review",
            error_message="normalized name matched without a strong identity signal",
            target_knowledge_item_id=target_id,
        )
        await uow.commit()
    return batch.id, item.id, document_id, chunk_ids


async def _candidates(batch_id: uuid.UUID, item_id: uuid.UUID):
    return (await knowledge_service.list_resume_candidates(batch_id))[item_id]


async def _pick(item_id, org_id, user_id, batch_id, employee_id, **overrides):
    listed = await _candidates(batch_id, item_id)
    shown = next(c for c in listed.candidates if c.knowledge_item_id == employee_id)
    kwargs = {
        "target_fingerprint": shown.fingerprint,
        "target_employee_id": employee_id,
        "candidates_fingerprint": listed.fingerprint,
    }
    kwargs.update(overrides)
    return await knowledge_service.approve_import_item(item_id, org_id, user_id, **kwargs)


async def _as_new(item_id, org_id, user_id, batch_id, fingerprint=None):
    if fingerprint is None:
        fingerprint = (await _candidates(batch_id, item_id)).fingerprint
    return await knowledge_service.import_item_as_new_employee(
        item_id, org_id, user_id, fingerprint
    )


async def _employee_row(employee_id: uuid.UUID) -> Employee:
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(employee_id)
    assert employee is not None
    return employee


async def _item(item_id: uuid.UUID):
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
    assert item is not None
    return item


async def _audit(target_id: uuid.UUID, action: str) -> list[AuditEvent]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(AuditEvent).where(AuditEvent.target_id == target_id, AuditEvent.action == action)
        )
        return list(rows.scalars().all())


async def _merge_events(item_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == item_id,
                OutboxEvent.event_type == EVENT_RESUME_MERGE_APPROVED,
            )
        )
        return len(rows.scalars().all())


async def _employee_count(org_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(func.count()).select_from(Employee).where(Employee.organization_id == org_id)
            )
        ).scalar_one()


async def _chunk_ids(employee_id: uuid.UUID) -> set[uuid.UUID]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(KnowledgeItemChunk.chunk_id).where(
                KnowledgeItemChunk.knowledge_item_id == employee_id
            )
        )
        return set(rows.scalars().all())


async def _reimport(org_id, user_id, document_id, chunk_ids, profile):
    batch = await knowledge_service.create_import_batch(org_id, "bulk_resumes", None, user_id)
    return await knowledge_service.import_resume(
        org_id, batch.id, f"{document_id}/again.docx", document_id, profile, chunk_ids
    )


# --------------------------------------------------------------- candidates


async def test_the_listing_offers_every_employee_the_resume_matches():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    second = await _employee(org_id, current_title="Engineer")
    await _employee(org_id, name="Jordan Lee")
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())

    listed = await _candidates(batch_id, item_id)
    assert {c.knowledge_item_id for c in listed.candidates} == {first, second}
    assert all(c.signals == ["name"] for c in listed.candidates)
    assert listed.strong_match is False
    by_id = {c.knowledge_item_id: c for c in listed.candidates}
    title = next(e for e in by_id[second].changes if e.field == "current_title")
    assert (title.on_file, title.from_resume, title.changes) == ("Engineer", "Lead Analyst", True)
    assert by_id[first].fingerprint == knowledge_service.resume_target_fingerprint(
        await _employee_row(first)
    )


async def test_decided_items_and_other_import_types_list_no_candidates():
    org_id, user_id = await _org()
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    await knowledge_service.reject_import_item(item_id, org_id, user_id, None)
    assert await knowledge_service.list_resume_candidates(batch_id) == {}
    master_batch, _, _, _ = await _review_item(
        org_id, user_id, _profile(), import_type="employee_master_xlsx"
    )
    assert await knowledge_service.list_resume_candidates(master_batch) == {}


# --------------------------------------------------------------------- pick


async def test_a_pick_merges_into_the_chosen_employee_only():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    second = await _employee(org_id, current_title="Engineer")
    batch_id, item_id, document_id, chunk_ids = await _review_item(org_id, user_id, _profile())

    updated = await _pick(item_id, org_id, user_id, batch_id, second)

    assert updated.status == "approved"
    assert updated.target_knowledge_item_id == second
    chosen = await _employee_row(second)
    assert chosen.current_title == "Lead Analyst"
    assert chosen.resume_document_id == document_id
    assert set(chunk_ids) <= await _chunk_ids(second)
    other = await _employee_row(first)
    assert other.current_title == "Analyst" and other.resume_document_id is None
    [approve] = await _audit(item_id, "knowledge_import_item_approve")
    assert approve.event_metadata["decision"] == "pick"
    assert approve.event_metadata["employee"] == str(second)
    assert sorted(approve.event_metadata["candidates"]) == sorted([str(first), str(second)])
    assert len(await _audit(second, "employee_resume_merge")) == 1
    assert await _merge_events(item_id) == 1


async def test_a_pick_can_overrule_the_employee_the_item_names():
    org_id, user_id = await _org()
    named = await _employee(org_id)
    other = await _employee(org_id, current_title="Engineer")
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile(), target_id=named)

    updated = await _pick(item_id, org_id, user_id, batch_id, other)

    assert updated.target_knowledge_item_id == other
    assert (await _employee_row(other)).current_title == "Lead Analyst"
    assert (await _employee_row(named)).current_title == "Analyst"


async def test_a_pick_of_an_employee_the_resume_does_not_match_is_refused():
    org_id, user_id = await _org()
    await _employee(org_id)
    stranger = await _employee(org_id, name="Jordan Lee")
    elsewhere_org, _ = await _org()
    elsewhere = await _employee(elsewhere_org)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    listed = await _candidates(batch_id, item_id)

    for employee_id in (stranger, elsewhere):
        with pytest.raises(knowledge_service.ImportItemNotACandidateError):
            await knowledge_service.approve_import_item(
                item_id,
                org_id,
                user_id,
                target_fingerprint=knowledge_service.resume_target_fingerprint(
                    await _employee_row(employee_id)
                ),
                target_employee_id=employee_id,
                candidates_fingerprint=listed.fingerprint,
            )
    assert (await _item(item_id)).status == "needs_review"


async def test_a_candidate_list_that_changed_since_it_was_shown_is_refused():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    listed = await _candidates(batch_id, item_id)
    shown = next(c for c in listed.candidates if c.knowledge_item_id == first)
    await _employee(org_id)  # a third Priya Shah arrives

    with pytest.raises(knowledge_service.ImportItemCandidatesChangedError):
        await knowledge_service.approve_import_item(
            item_id,
            org_id,
            user_id,
            target_fingerprint=shown.fingerprint,
            target_employee_id=first,
            candidates_fingerprint=listed.fingerprint,
        )
    with pytest.raises(knowledge_service.ImportItemCandidatesChangedError):
        await _as_new(item_id, org_id, user_id, batch_id, fingerprint=listed.fingerprint)
    assert (await _item(item_id)).status == "needs_review"
    assert (await _employee_row(first)).current_title == "Analyst"


async def test_a_pick_needs_both_fingerprints():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    for missing in ("target_fingerprint", "candidates_fingerprint"):
        with pytest.raises(knowledge_service.ImportItemFingerprintRequiredError):
            await _pick(item_id, org_id, user_id, batch_id, first, **{missing: None})


async def test_a_pick_of_an_archived_candidate_is_refused():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(first, status="archived")
        await uow.commit()
    assert next(
        c for c in (await _candidates(batch_id, item_id)).candidates if c.knowledge_item_id == first
    ).archived
    with pytest.raises(knowledge_service.EmployeeArchivedError):
        await _pick(item_id, org_id, user_id, batch_id, first)


async def test_a_replayed_pick_writes_nothing_and_a_different_one_is_refused():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    second = await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    listed = await _candidates(batch_id, item_id)
    by_id = {c.knowledge_item_id: c for c in listed.candidates}
    await _pick(item_id, org_id, user_id, batch_id, first)

    again = await knowledge_service.approve_import_item(
        item_id,
        org_id,
        user_id,
        target_fingerprint=by_id[first].fingerprint,
        target_employee_id=first,
        candidates_fingerprint=listed.fingerprint,
    )
    assert again.target_knowledge_item_id == first
    assert len(await _audit(item_id, "knowledge_import_item_approve")) == 1
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await knowledge_service.approve_import_item(
            item_id,
            org_id,
            user_id,
            target_fingerprint=by_id[second].fingerprint,
            target_employee_id=second,
            candidates_fingerprint=listed.fingerprint,
        )
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await _as_new(item_id, org_id, user_id, batch_id, fingerprint=listed.fingerprint)


async def test_two_concurrent_picks_merge_once():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    results = await asyncio.gather(
        _pick(item_id, org_id, user_id, batch_id, first),
        _pick(item_id, org_id, user_id, batch_id, first),
    )
    assert {r.status for r in results} == {"approved"}
    assert len(await _audit(first, "employee_resume_merge")) == 1
    assert await _merge_events(item_id) == 1


async def test_choosing_an_employee_is_only_for_resume_items():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    _, item_id, _, _ = await _review_item(
        org_id, user_id, _profile(), import_type="employee_master_xlsx"
    )
    with pytest.raises(knowledge_service.ImportItemTypeUnsupportedError):
        await knowledge_service.approve_import_item(
            item_id,
            org_id,
            user_id,
            target_fingerprint="x",
            target_employee_id=first,
            candidates_fingerprint="y",
        )
    with pytest.raises(knowledge_service.ImportItemTypeUnsupportedError):
        await knowledge_service.import_item_as_new_employee(item_id, org_id, user_id, "y")


# --------------------------------------------------------- import as new


async def test_import_as_new_makes_an_employee_from_the_resume():
    org_id, user_id = await _org()
    await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, document_id, chunk_ids = await _review_item(
        org_id, user_id, _profile(email="priya.new@example.test")
    )
    before = await _employee_count(org_id)

    updated = await _as_new(item_id, org_id, user_id, batch_id)

    assert updated.status == "approved"
    assert await _employee_count(org_id) == before + 1
    created = await _employee_row(updated.target_knowledge_item_id)
    assert created.name == NAME
    assert created.email == "priya.new@example.test"
    assert created.current_title == "Lead Analyst"
    assert created.resume_document_id == document_id
    assert await _chunk_ids(created.knowledge_item_id) == set(chunk_ids)
    [approve] = await _audit(item_id, "knowledge_import_item_approve")
    assert approve.event_metadata["decision"] == "new"
    assert approve.event_metadata["employee"] == str(created.knowledge_item_id)
    assert await _merge_events(item_id) == 1


@pytest.mark.parametrize("signal", ["email", "resume_document_id"])
async def test_import_as_new_is_refused_when_a_strong_signal_matched(signal):
    org_id, user_id = await _org()
    document = await _document(org_id, user_id)
    if signal == "email":
        await _employee(org_id, name="Someone Else", email="priya@example.test")
    else:
        await _employee(org_id, name="Someone Else", resume_document_id=document[0])
    batch_id, item_id, _, _ = await _review_item(
        org_id, user_id, _profile(email="Priya@Example.test"), document=document
    )
    assert (await _candidates(batch_id, item_id)).strong_match
    before = await _employee_count(org_id)
    with pytest.raises(knowledge_service.ImportItemStrongMatchError):
        await _as_new(item_id, org_id, user_id, batch_id)
    assert await _employee_count(org_id) == before
    assert (await _item(item_id)).status == "needs_review"


async def test_import_as_new_is_refused_when_the_document_already_made_an_employee():
    """An employee made from this document once (its fingerprint) whose
    resume document has since been replaced: no strong signal, but "new"
    would converge on them."""
    org_id, user_id = await _org()
    document_id, chunk_ids = await _document(org_id, user_id)
    other_document, _ = await _document(org_id, user_id)
    async with UnitOfWork() as uow:
        await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Earlier Import",
            content_fingerprint=knowledge_service.resume_employee_fingerprint(org_id, document_id),
            title="Earlier Import",
            resume_document_id=other_document,
        )
        await uow.commit()
    batch_id, item_id, _, _ = await _review_item(
        org_id, user_id, _profile(), document=(document_id, chunk_ids)
    )
    await _employee(org_id)
    with pytest.raises(knowledge_service.ImportItemStrongMatchError):
        await _as_new(item_id, org_id, user_id, batch_id)


async def test_a_replayed_import_as_new_makes_one_employee():
    org_id, user_id = await _org()
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    fingerprint = (await _candidates(batch_id, item_id)).fingerprint
    first = await _as_new(item_id, org_id, user_id, batch_id, fingerprint)
    before = await _employee_count(org_id)
    again = await _as_new(item_id, org_id, user_id, batch_id, fingerprint)
    assert again.target_knowledge_item_id == first.target_knowledge_item_id
    assert await _employee_count(org_id) == before
    assert len(await _audit(item_id, "knowledge_import_item_approve")) == 1


async def test_import_as_new_after_a_pick_is_refused():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    fingerprint = (await _candidates(batch_id, item_id)).fingerprint
    await _pick(item_id, org_id, user_id, batch_id, first)
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await _as_new(item_id, org_id, user_id, batch_id, fingerprint)


async def test_import_as_new_and_the_new_branch_make_the_same_employee():
    """One definition (resume_employee_fields): a resume the import makes a
    new employee from, and the same profile imported as new by a reviewer,
    give the same values (strings trimmed in both)."""
    profile = _profile(current_title="  Lead Analyst  ", email=" priya.solo@example.test ")
    org_a, user_a = await _org()
    document_a, chunks_a = await _document(org_a, user_a)
    batch_a = await knowledge_service.create_import_batch(org_a, "bulk_resumes", None, user_a)
    made = await knowledge_service.import_resume(
        org_a, batch_a.id, f"{document_a}/a.docx", document_a, profile, chunks_a
    )
    org_b, user_b = await _org()
    await _employee(org_b, name="Priya  Shah")
    batch_b, item_b, _, _ = await _review_item(org_b, user_b, profile)
    imported = await _as_new(item_b, org_b, user_b, batch_b)

    def shown(employee: Employee) -> tuple:
        return (
            employee.name,
            employee.email,
            employee.current_title,
            employee.professional_location,
            float(employee.years_experience) if employee.years_experience is not None else None,
        )

    a = await _employee_row(made.target_knowledge_item_id)
    b = await _employee_row(imported.target_knowledge_item_id)
    assert shown(a) == shown(b) == (NAME, "priya.solo@example.test", "Lead Analyst", "Pune", 9.0)


# -------------------------------------------------- the same input again


async def test_after_a_pick_the_same_resume_again_merges_without_review():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, document_id, chunk_ids = await _review_item(org_id, user_id, _profile())
    await _pick(item_id, org_id, user_id, batch_id, first)

    again = await _reimport(org_id, user_id, document_id, chunk_ids, _profile())
    assert (again.status, again.target_knowledge_item_id) == ("approved", first)


async def test_after_import_as_new_the_same_resume_again_merges_without_review():
    org_id, user_id = await _org()
    await _employee(org_id)
    batch_id, item_id, document_id, chunk_ids = await _review_item(org_id, user_id, _profile())
    created = await _as_new(item_id, org_id, user_id, batch_id)

    again = await _reimport(org_id, user_id, document_id, chunk_ids, _profile())
    assert (again.status, again.target_knowledge_item_id) == (
        "approved",
        created.target_knowledge_item_id,
    )


async def test_a_pick_keeps_an_email_another_employee_holds():
    """A strong conflict: the resume's document is Ravi's, its email Jordan's.
    Picking Ravi merges the resume but keeps Ravi's email. Writing Jordan's
    onto Ravi would leave two employees with one email, and Jordan's own next
    resume would then be a conflict too, in review on every import. The same
    conflicting resume still conflicts on its own: it carries another
    person's email (design section 8)."""
    org_id, user_id = await _org()
    document = await _document(org_id, user_id)
    jordan = await _employee(org_id, name="Jordan Lee", email="jordan@example.test")
    ravi = await _employee(
        org_id, name="Ravi Das", email="ravi@example.test", resume_document_id=document[0]
    )
    profile = _profile(name="Ravi Das", email="jordan@example.test")
    batch_id, item_id, document_id, chunk_ids = await _review_item(
        org_id, user_id, profile, document=document
    )
    listed = await _candidates(batch_id, item_id)
    assert {c.knowledge_item_id for c in listed.candidates} == {jordan, ravi}
    email = next(
        e
        for e in next(c for c in listed.candidates if c.knowledge_item_id == ravi).changes
        if e.field == "email"
    )
    assert (email.changes, email.held_by_other) == (False, True)

    await _pick(item_id, org_id, user_id, batch_id, ravi)
    merged = await _employee_row(ravi)
    assert merged.email == "ravi@example.test"
    assert merged.current_title == "Lead Analyst"

    jordan_document, jordan_chunks = await _document(org_id, user_id)
    jordans_own = await _reimport(
        org_id,
        user_id,
        jordan_document,
        jordan_chunks,
        _profile(name="Jordan Lee", email="jordan@example.test"),
    )
    assert (jordans_own.status, jordans_own.target_knowledge_item_id) == ("approved", jordan)
    again = await _reimport(org_id, user_id, document_id, chunk_ids, profile)
    assert again.status == "needs_review", "this resume itself carries Jordan's email"


async def test_an_approval_keeps_an_email_another_employee_holds():
    """Through PR 18's own approval (the item names the employee), so this
    reads the merge's rule on main as it is: a resume whose email is another
    employee's does not give it to the one approved into."""
    org_id, user_id = await _org()
    await _employee(org_id, name="Jordan Lee", email="jordan@example.test")
    named = await _employee(org_id, email="priya@example.test")
    _, item_id, _, _ = await _review_item(
        org_id, user_id, _profile(email="jordan@example.test"), target_id=named
    )
    await knowledge_service.approve_import_item(
        item_id,
        org_id,
        user_id,
        target_fingerprint=knowledge_service.resume_target_fingerprint(await _employee_row(named)),
    )
    merged = await _employee_row(named)
    assert merged.email == "priya@example.test"
    assert merged.current_title == "Lead Analyst"


async def test_an_employees_own_email_is_not_held_by_another():
    """The held-email rule counts other employees only: a candidate whose own
    email the resume carries shows it as kept because it is the same, not as
    held by someone else."""
    org_id, user_id = await _org()
    own = await _employee(org_id, email="priya@example.test")
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(
        org_id, user_id, _profile(email="priya@example.test")
    )
    listed = await _candidates(batch_id, item_id)
    email = next(
        e
        for e in next(c for c in listed.candidates if c.knowledge_item_id == own).changes
        if e.field == "email"
    )
    assert (email.changes, email.held_by_other) == (False, False)


# ------------------------------------------------------------------- note 1


async def test_the_merge_reads_strings_trimmed_and_a_value_of_spaces_as_blank():
    """Through PR 18's own approval (the item names the employee), so this
    reads the merge's rule on main as it is."""
    org_id, user_id = await _org()
    employee_id = await _employee(org_id, current_title="Analyst  ")
    profile = _profile(current_title="Analyst", professional_location="   ")
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, profile, target_id=employee_id)
    [(_, target)] = [
        row
        for row in await knowledge_service.list_import_items_with_targets(batch_id)
        if row[0].id == item_id
    ]
    assert target is not None and target.changes is not None
    changes = {e.field: e for e in target.changes}
    assert changes["current_title"].changes is False, "only surrounding spaces differ"
    assert changes["professional_location"].from_resume is None, "only spaces is blank"
    assert changes["professional_location"].changes is False

    trimmed = _profile(current_title="  Principal Analyst ", professional_location=None)
    _, item2, _, _ = await _review_item(org_id, user_id, trimmed, target_id=employee_id)
    await knowledge_service.approve_import_item(
        item2,
        org_id,
        user_id,
        target_fingerprint=knowledge_service.resume_target_fingerprint(
            await _employee_row(employee_id)
        ),
    )
    assert (await _employee_row(employee_id)).current_title == "Principal Analyst"


# --------------------------------------------------------------- lock order


async def test_a_pick_refuses_a_pending_item_without_waiting_for_the_employee_lock():
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    listed = await _candidates(batch_id, item_id)
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item_id, status="pending")
        await uow.commit()

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": first},
        )
        with pytest.raises(knowledge_service.ImportItemNotInReviewError):
            await asyncio.wait_for(
                knowledge_service.approve_import_item(
                    item_id,
                    org_id,
                    user_id,
                    target_fingerprint="x",
                    target_employee_id=first,
                    candidates_fingerprint=listed.fingerprint,
                ),
                timeout=10,
            )
        await holder.rollback()


async def test_a_pick_waits_for_the_chosen_employee_then_sees_its_values():
    """The item first, then the chosen employee: a merge holding that
    employee is waited for, and the pick then compares the fingerprint with
    the values the holder left."""
    org_id, user_id = await _org()
    first = await _employee(org_id)
    await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": first},
        )
        pick = asyncio.create_task(_pick(item_id, org_id, user_id, batch_id, first))
        await asyncio.sleep(1.0)
        assert not pick.done(), "the pick waits for the chosen employee's row"
        await holder.session.execute(
            text(
                "UPDATE employees SET professional_location = 'Goa' WHERE knowledge_item_id = :id"
            ),
            {"id": first},
        )
        await holder.commit()

    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await asyncio.wait_for(pick, timeout=10)
    assert (await _item(item_id)).status == "needs_review"


async def test_import_as_new_takes_no_employee_lock():
    """Import as new locks the item only: a transaction holding a candidate
    employee's row does not hold it up."""
    org_id, user_id = await _org()
    first = await _employee(org_id)
    batch_id, item_id, _, _ = await _review_item(org_id, user_id, _profile())
    fingerprint = (await _candidates(batch_id, item_id)).fingerprint

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": first},
        )
        updated = await asyncio.wait_for(
            _as_new(item_id, org_id, user_id, batch_id, fingerprint), timeout=10
        )
        await holder.rollback()
    assert updated.status == "approved"
