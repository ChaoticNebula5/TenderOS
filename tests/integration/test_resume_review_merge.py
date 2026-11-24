"""Plan item 80 (PR 18, user decisions 2026-11-18): approving a resume review
item merges the resume into the employee it names, through the same merge
as import_resume's automatic DUPLICATE branch; a verified profile changes
only after a person saw the change.

Real Postgres. Self-built helpers, no imports from other test modules (this
suite's convention). Every fixture value is fake.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import event, select, text
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.knowledge import KnowledgeItemChunk
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.session import engine
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_RESUME_MERGE_APPROVED
from tenderos_backend.domain.services import document_service, knowledge_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender

pytestmark = pytest.mark.asyncio

NAME = "Morgan Vale"


def _profile(**overrides: object) -> ExtractedEmployeeProfile:
    fields: dict[str, object] = {
        "name": NAME,
        "email": "morgan.vale@example.test",
        "current_title": "Principal Engineer",
        "professional_location": None,
        "years_experience": 12.0,
        "project_mentions": [
            {"name": "Harbour Data Platform", "client": "Harbour", "year": "2025"}
        ],
        "confidence": 0.9,
    }
    fields.update(overrides)
    return ExtractedEmployeeProfile.model_validate(fields)


async def _org() -> tuple[uuid.UUID, uuid.UUID]:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Resume Review Org",
    )
    return registered.organization.id, registered.user.id


async def _document(org_id: uuid.UUID, user_id: uuid.UUID, texts: list[str]):
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
    chunks = []
    async with UnitOfWork() as uow:
        for i, chunk_text in enumerate(texts):
            chunks.append(
                await uow.documents.create_chunk(
                    document_id=document.id,
                    chunk_index=i,
                    chunk_text=chunk_text,
                    normalized_text=chunk_text.lower(),
                    content_hash=uuid.uuid4().hex,
                )
            )
        await uow.commit()
    return document, chunks


async def _employee(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    verified: bool,
    current_title: str | None = "Engineer",
    professional_location: str | None = "Pune",
    email: str | None = None,
    name: str = NAME,
):
    document, chunks = await _document(org_id, user_id, ["Engineer at Example."])
    async with UnitOfWork() as uow:
        item, _employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
            email=email,
            current_title=current_title,
            professional_location=professional_location,
            resume_document_id=document.id,
            source_document_id=document.id,
        )
        await uow.knowledge.associate_knowledge_item_chunks(item.id, [c.id for c in chunks])
        if verified:
            await uow.knowledge.update_employee(
                item.id, profile_review_status="verified", eligible_for_bid=True
            )
        await uow.commit()
    return item.id, document.id


async def _batch(org_id: uuid.UUID, user_id: uuid.UUID, import_type: str = "bulk_resumes"):
    batch = await knowledge_service.create_import_batch(org_id, import_type, None, user_id)
    return batch.id


async def _review_item(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    target_id: uuid.UUID | None,
    profile: ExtractedEmployeeProfile,
    *,
    with_document: bool = True,
):
    document, chunks = await _document(
        org_id, user_id, ["Principal Engineer, 12 years.", "Led the Harbour Data Platform."]
    )
    batch_id = await _batch(org_id, user_id)
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=f"{document.id}/{document.filename}",
            extracted_payload=profile.model_dump(),
            source_document_id=document.id if with_document else None,
        )
        await uow.knowledge.update_import_item(
            item.id,
            status="needs_review",
            error_message="normalized name matched without a strong identity signal",
            target_knowledge_item_id=target_id,
        )
        await uow.commit()
    return item.id, document.id, chunks


async def _employee_row(employee_id: uuid.UUID):
    async with UnitOfWork() as uow:
        return await uow.knowledge.get_employee(employee_id)


async def _fingerprint(employee_id: uuid.UUID) -> str:
    employee = await _employee_row(employee_id)
    assert employee is not None
    return knowledge_service.resume_target_fingerprint(employee)


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


async def _chunk_ids(employee_id: uuid.UUID) -> set[uuid.UUID]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(KnowledgeItemChunk.chunk_id).where(
                KnowledgeItemChunk.knowledge_item_id == employee_id
            )
        )
        return set(rows.scalars().all())


async def _item_status(item_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
    assert item is not None
    return item.status


async def _citing_bid(org_id: uuid.UUID, user_id: uuid.UUID, employee_id: uuid.UUID) -> uuid.UUID:
    tender = await create_tender(organization_id=org_id, name="Citing bid", created_by=user_id)
    async with UnitOfWork() as uow:
        await uow.compliance.create_evidence_link(
            tender_id=tender.id,
            source_kind="employee",
            content_fingerprint=uuid.uuid4().hex,
            knowledge_item_id=employee_id,
            verified=True,
            validity_status="valid",
        )
        await uow.commit()
    return tender.id


async def _version(tender_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
    assert tender is not None
    return tender.state_version


# ---------------------------------------------------------------- approval


async def test_approving_a_resume_item_merges_it_into_the_employee_by_the_rule():
    org_id, user_id = await _org()
    employee_id, old_document = await _employee(org_id, user_id, verified=True)
    old_chunks = await _chunk_ids(employee_id)
    profile = _profile()
    item_id, new_document, new_chunks = await _review_item(org_id, user_id, employee_id, profile)

    approved = await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )

    assert approved.status == "approved"
    employee = await _employee_row(employee_id)
    assert employee is not None
    # The resume's value wherever it has one (Q2); blank keeps what is on file.
    assert employee.email == "morgan.vale@example.test"  # filled
    assert employee.current_title == "Principal Engineer"  # overwritten
    assert employee.professional_location == "Pune"  # resume blank: kept
    assert float(employee.years_experience) == 12.0  # filled
    assert employee.name == NAME
    assert employee.resume_document_id == new_document != old_document
    assert employee.parsed_profile == profile.model_dump()
    # Q1: the review status and eligibility are kept.
    assert employee.profile_review_status == "verified"
    assert employee.eligible_for_bid is True
    # Additive: the old resume's chunks stay, the new one's are added.
    assert await _chunk_ids(employee_id) == old_chunks | {c.id for c in new_chunks}

    [merge] = await _audit(employee_id, "employee_resume_merge")
    assert (merge.actor_type, merge.actor_id) == ("user", str(user_id))
    assert merge.before["current_title"] == "Engineer"
    assert merge.after["current_title"] == "Principal Engineer"
    assert merge.event_metadata["fields_changed"] == sorted(
        ["email", "current_title", "years_experience"]
    )
    assert merge.event_metadata["chunks_added"] == len(new_chunks)
    [approve] = await _audit(item_id, "knowledge_import_item_approve")
    assert approve.event_metadata["employee"] == str(employee_id)
    assert await _merge_events(item_id) == 1


async def test_a_title_change_bumps_the_bids_projecting_the_employee_and_nothing_else_does():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    bid = await _citing_bid(org_id, user_id, employee_id)

    # Title unchanged (the resume repeats it): email and years only.
    same_title, _, _ = await _review_item(
        org_id, user_id, employee_id, _profile(current_title="Engineer")
    )
    start = await _version(bid)
    await knowledge_service.approve_import_item(
        same_title, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )
    assert await _version(bid) == start, "email and years are not on the graph node"

    new_title, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    await knowledge_service.approve_import_item(
        new_title, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )
    assert await _version(bid) == start + 1, "the title is (plan item 95)"


async def test_a_stale_fingerprint_is_refused_and_nothing_is_written():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    shown = await _fingerprint(employee_id)
    # Someone edits the profile after the reviewer loaded the item.
    await knowledge_service.patch_employee_profile(
        employee_id, org_id, user_id, professional_location="Mumbai"
    )
    before = await _employee_row(employee_id)

    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await knowledge_service.approve_import_item(
            item_id, org_id, user_id, target_fingerprint=shown
        )

    after = await _employee_row(employee_id)
    assert after is not None and before is not None
    assert (after.current_title, after.resume_document_id) == (
        before.current_title,
        before.resume_document_id,
    )
    assert await _item_status(item_id) == "needs_review"
    assert await _audit(employee_id, "employee_resume_merge") == []
    assert await _merge_events(item_id) == 0
    # With the current values it goes through.
    await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )
    assert await _item_status(item_id) == "approved"


@pytest.mark.parametrize(
    "change",
    [
        {"email": "other@example.test"},
        {"current_title": "Staff Engineer"},
        {"professional_location": "Delhi"},
        {"years_experience": 3},
        {"resume_document_id": "new"},
        {"profile_review_status": "stale"},
    ],
    ids=lambda c: next(iter(c)),
)
async def test_the_fingerprint_covers_each_value_the_panel_shows(change):
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    shown = await _fingerprint(employee_id)
    if change.get("resume_document_id") == "new":
        document, _ = await _document(org_id, user_id, ["another resume"])
        change = {"resume_document_id": document.id}
    async with UnitOfWork() as uow:
        await uow.knowledge.update_employee(employee_id, **change)
        await uow.commit()
    assert await _fingerprint(employee_id) != shown


async def test_a_resume_approval_without_a_fingerprint_is_refused():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    with pytest.raises(knowledge_service.ImportItemFingerprintRequiredError):
        await knowledge_service.approve_import_item(item_id, org_id, user_id)
    assert await _item_status(item_id) == "needs_review"


async def test_an_item_naming_no_employee_is_refused():
    org_id, user_id = await _org()
    item_id, _, _ = await _review_item(org_id, user_id, None, _profile())
    with pytest.raises(knowledge_service.ImportItemNoTargetError):
        await knowledge_service.approve_import_item(item_id, org_id, user_id, "anything")
    assert await _item_status(item_id) == "needs_review"


async def test_an_item_with_no_source_document_is_refused():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(
        org_id, user_id, employee_id, _profile(), with_document=False
    )
    with pytest.raises(knowledge_service.ImportItemSourceMissingError):
        await knowledge_service.approve_import_item(
            item_id, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
        )


async def test_an_archived_target_is_refused():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(employee_id, status="archived")
        await uow.commit()
    with pytest.raises(knowledge_service.EmployeeArchivedError):
        await knowledge_service.approve_import_item(
            item_id, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
        )
    assert await _item_status(item_id) == "needs_review"


async def test_a_replayed_approval_writes_nothing():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    shown = await _fingerprint(employee_id)
    await knowledge_service.approve_import_item(item_id, org_id, user_id, target_fingerprint=shown)

    # The same request again (a double click, a retry after a timeout): the
    # fingerprint no longer matches, and it does not matter.
    replay = await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=shown
    )

    assert replay.status == "approved"
    assert len(await _audit(item_id, "knowledge_import_item_approve")) == 1
    assert len(await _audit(employee_id, "employee_resume_merge")) == 1
    assert await _merge_events(item_id) == 1


async def test_two_concurrent_approvals_merge_once():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    shown = await _fingerprint(employee_id)

    results = await asyncio.gather(
        knowledge_service.approve_import_item(item_id, org_id, user_id, target_fingerprint=shown),
        knowledge_service.approve_import_item(item_id, org_id, user_id, target_fingerprint=shown),
    )

    assert [r.status for r in results] == ["approved", "approved"]
    assert len(await _audit(employee_id, "employee_resume_merge")) == 1
    assert len(await _audit(item_id, "knowledge_import_item_approve")) == 1
    assert await _merge_events(item_id) == 1


async def test_decided_items_are_refused_the_other_decision_and_replays_write_nothing():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    rejected, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    await knowledge_service.reject_import_item(rejected, org_id, user_id, "different person")
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await knowledge_service.approve_import_item(
            rejected, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
        )
    await knowledge_service.reject_import_item(rejected, org_id, user_id, "again")
    assert len(await _audit(rejected, "knowledge_import_item_reject")) == 1

    approved, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    await knowledge_service.approve_import_item(
        approved, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await knowledge_service.reject_import_item(approved, org_id, user_id, "too late")


async def test_a_failed_item_can_still_be_rejected_but_not_approved():
    org_id, user_id = await _org()
    item_id, _, _ = await _review_item(org_id, user_id, None, _profile())
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item_id, status="failed")
        await uow.commit()
    with pytest.raises(knowledge_service.ImportItemNotInReviewError):
        await knowledge_service.approve_import_item(item_id, org_id, user_id, "x")
    rejected = await knowledge_service.reject_import_item(item_id, org_id, user_id, "unreadable")
    assert rejected.status == "rejected"


async def test_an_employee_master_review_item_needs_the_fingerprint_and_queues_no_extraction():
    """Plan item 112 changed this: a master item no longer approves status
    only. It applies its row, so it needs the fingerprint of what was shown
    (tests/integration/test_master_and_certificate_approval.py), and it
    queues no WORKED_ON extraction: a master row carries no resume."""
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    batch_id = await _batch(org_id, user_id, "employee_master_xlsx")
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id, source_ref="row_2", extracted_payload={"name": NAME}
        )
        await uow.knowledge.update_import_item(
            item.id, status="needs_review", target_knowledge_item_id=employee_id
        )
        await uow.commit()
    with pytest.raises(knowledge_service.ImportItemFingerprintRequiredError):
        await knowledge_service.approve_import_item(item.id, org_id, user_id)
    employee = await _employee_row(employee_id)
    assert employee is not None
    async with UnitOfWork() as uow:
        knowledge_item = await uow.knowledge.get_knowledge_item(employee_id)
    assert knowledge_item is not None
    approved = await knowledge_service.approve_import_item(
        item.id,
        org_id,
        user_id,
        target_fingerprint=knowledge_service.master_target_fingerprint(
            employee, knowledge_item.status
        ),
    )
    assert approved.status == "approved"
    assert await _merge_events(item.id) == 0


# ---------------------------------------------------- the automatic branch


async def _import_duplicate(org_id, user_id, employee_id, document_id, profile):
    """import_resume reaching DUPLICATE: the employee's own resume document
    (a strong signal) plus the name, as replace_employee_resume's
    re-extraction reaches it."""
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document_id)
    batch_id = await _batch(org_id, user_id)
    return await knowledge_service.import_resume(
        org_id,
        batch_id,
        f"{document_id}/re-extraction.docx",
        document_id,
        profile,
        [c.id for c in chunks],
    )


async def test_the_automatic_branch_sends_an_overwrite_of_a_verified_profile_to_review():
    org_id, user_id = await _org()
    employee_id, document_id = await _employee(org_id, user_id, verified=True)
    before = await _employee_row(employee_id)

    item = await _import_duplicate(org_id, user_id, employee_id, document_id, _profile())

    assert item.status == "needs_review"
    assert item.target_knowledge_item_id == employee_id
    assert item.source_document_id == document_id
    assert item.error_message is not None
    assert "would change the verified profile's title" in item.error_message
    after = await _employee_row(employee_id)
    assert after is not None and before is not None
    assert (after.current_title, after.email, after.parsed_profile) == (
        before.current_title,
        before.email,
        before.parsed_profile,
    )
    assert await _audit(employee_id, "employee_resume_merge") == []
    # And a person can approve it, with what they saw.
    await knowledge_service.approve_import_item(
        item.id, org_id, user_id, target_fingerprint=await _fingerprint(employee_id)
    )
    merged = await _employee_row(employee_id)
    assert merged is not None and merged.current_title == "Principal Engineer"


async def test_the_automatic_branch_still_fills_blanks_on_a_verified_profile():
    org_id, user_id = await _org()
    employee_id, document_id = await _employee(org_id, user_id, verified=True)
    # The resume only fills blanks (email, years) and repeats the title.
    item = await _import_duplicate(
        org_id, user_id, employee_id, document_id, _profile(current_title="Engineer")
    )

    assert item.status == "approved"
    employee = await _employee_row(employee_id)
    assert employee is not None
    assert employee.email == "morgan.vale@example.test"
    assert employee.profile_review_status == "verified"
    [merge] = await _audit(employee_id, "employee_resume_merge")
    assert (merge.actor_type, merge.actor_id) == ("system", "resume_import")
    assert merge.event_metadata["import_item_id"] == str(item.id)
    # Its own resume again: every chunk is already linked, none is added.
    assert merge.event_metadata["chunks_added"] == 0


async def test_the_automatic_branch_merges_an_unverified_profile_with_a_system_audit_row():
    org_id, user_id = await _org()
    employee_id, document_id = await _employee(org_id, user_id, verified=False)
    item = await _import_duplicate(org_id, user_id, employee_id, document_id, _profile())

    assert item.status == "approved"
    employee = await _employee_row(employee_id)
    assert employee is not None and employee.current_title == "Principal Engineer"
    [merge] = await _audit(employee_id, "employee_resume_merge")
    assert merge.actor_type == "system"
    assert merge.event_metadata["fields_changed"] == ["current_title", "email", "years_experience"]


async def test_approval_and_the_automatic_branch_merge_identically():
    """One merge function, two callers: the same profile into two identical
    unverified employees, once automatically and once by approval. Plan item
    111: in two organizations, since the merge keeps an email another
    employee of the same organization holds."""
    org_id, user_id = await _org()
    review_org, review_user = await _org()
    auto_id, auto_document = await _employee(org_id, user_id, verified=False, name="Auto Vale")
    review_id, _ = await _employee(review_org, review_user, verified=False, name="Review Vale")
    profile = _profile()

    await _import_duplicate(
        org_id, user_id, auto_id, auto_document, profile.model_copy(update={"name": "Auto Vale"})
    )
    item_id, _, _ = await _review_item(
        review_org, review_user, review_id, profile.model_copy(update={"name": "Review Vale"})
    )
    await knowledge_service.approve_import_item(
        item_id, review_org, review_user, target_fingerprint=await _fingerprint(review_id)
    )

    auto = await _employee_row(auto_id)
    reviewed = await _employee_row(review_id)
    assert auto is not None and reviewed is not None
    fields = ("email", "current_title", "professional_location", "years_experience")
    assert [getattr(auto, f) for f in fields] == [getattr(reviewed, f) for f in fields]
    assert auto.profile_review_status == reviewed.profile_review_status
    [auto_merge] = await _audit(auto_id, "employee_resume_merge")
    [review_merge] = await _audit(review_id, "employee_resume_merge")
    assert (
        auto_merge.event_metadata["fields_changed"] == review_merge.event_metadata["fields_changed"]
    )


async def test_import_resume_records_the_resume_document_on_its_item():
    org_id, user_id = await _org()
    document, chunks = await _document(org_id, user_id, ["Someone new."])
    batch_id = await _batch(org_id, user_id)
    item = await knowledge_service.import_resume(
        org_id,
        batch_id,
        f"{document.id}/{document.filename}",
        document.id,
        _profile(name=f"New Person {uuid.uuid4().hex[:6]}"),
        [c.id for c in chunks],
    )
    assert item.source_document_id == document.id


# ------------------------------------------------------- the review listing


async def test_the_listing_names_each_resume_items_employee_with_its_fingerprint():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
    assert item is not None

    [(listed, target)] = await knowledge_service.list_import_items_with_targets(item.batch_id)
    assert listed.id == item_id
    assert target is not None
    assert target.knowledge_item_id == employee_id
    assert target.current_title == "Engineer"
    assert target.fingerprint == await _fingerprint(employee_id)
    assert target.merged_into_existing is False

    await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=target.fingerprint
    )
    [(_, after)] = await knowledge_service.list_import_items_with_targets(item.batch_id)
    assert after is not None and after.merged_into_existing is True


async def test_an_employee_made_from_the_items_own_resume_is_not_shown_as_merged():
    org_id, user_id = await _org()
    document, chunks = await _document(org_id, user_id, ["Someone new."])
    batch_id = await _batch(org_id, user_id)
    await knowledge_service.import_resume(
        org_id,
        batch_id,
        f"{document.id}/{document.filename}",
        document.id,
        _profile(name=f"New Person {uuid.uuid4().hex[:6]}"),
        [c.id for c in chunks],
    )
    [(item, target)] = await knowledge_service.list_import_items_with_targets(batch_id)
    assert item.status == "approved"
    assert target is not None and target.merged_into_existing is False


async def test_other_import_types_list_no_target():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    batch_id = await _batch(org_id, user_id, "employee_master_xlsx")
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id, source_ref="row_2", extracted_payload={"name": NAME}
        )
        # It names the employee: only the import type keeps the target off.
        await uow.knowledge.update_import_item(item.id, target_knowledge_item_id=employee_id)
        await uow.commit()
    [(_, target)] = await knowledge_service.list_import_items_with_targets(batch_id)
    assert target is None


# --------------------------------------------------------------- lock order


async def test_approval_refuses_a_pending_item_without_waiting_for_the_employee_lock():
    """import_resume's automatic branch locks the employee, then writes its
    own new item, still pending. An approval of that item locks it first; if
    it then waited for the employee, the two would wait on each other. It
    refuses a pending item before asking, so it returns while the employee
    row is still locked."""
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item_id, status="pending")
        await uow.commit()

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": employee_id},
        )
        with pytest.raises(knowledge_service.ImportItemNotInReviewError):
            await asyncio.wait_for(
                knowledge_service.approve_import_item(item_id, org_id, user_id, "x"), timeout=10
            )
        await holder.rollback()


async def test_an_approval_waits_for_a_merge_holding_the_employee_then_sees_its_values():
    """The automatic branch's order (the employee, then its own item) with an
    approval of another item (that item, then the employee): the approval
    waits, the holder writes its own item and commits, and the approval then
    compares the fingerprint with the values the holder left."""
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=False)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    other_item, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    shown = await _fingerprint(employee_id)

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text("SELECT 1 FROM employees WHERE knowledge_item_id = :id FOR UPDATE"),
            {"id": employee_id},
        )
        approval = asyncio.create_task(
            knowledge_service.approve_import_item(
                item_id, org_id, user_id, target_fingerprint=shown
            )
        )
        await asyncio.sleep(1.0)
        assert not approval.done(), "the approval waits for the employee row"
        await holder.session.execute(
            text("UPDATE employees SET current_title = 'Changed' WHERE knowledge_item_id = :id"),
            {"id": employee_id},
        )
        await holder.session.execute(
            text("UPDATE knowledge_import_items SET status = 'approved' WHERE id = :id"),
            {"id": other_item},
        )
        await holder.commit()

    with pytest.raises(knowledge_service.ImportItemTargetChangedError):
        await asyncio.wait_for(approval, timeout=10)
    assert await _item_status(item_id) == "needs_review"


async def test_the_automatic_branch_judges_the_employee_under_its_row_lock():
    """While another transaction holds the employee (an approval or an edit
    mid-way) with the title the resume carries, the automatic branch waits
    for it and then sees that title: nothing left to overwrite, so it merges
    the blanks. Judged on the values read before the lock (the duplicate
    candidates query), it would see the old title and send the resume to
    review for a change that is no longer there."""
    org_id, user_id = await _org()
    employee_id, document_id = await _employee(org_id, user_id, verified=True)

    async with UnitOfWork() as holder:
        await holder.session.execute(
            text(
                "UPDATE employees SET current_title = 'Principal Engineer' "
                "WHERE knowledge_item_id = :id"
            ),
            {"id": employee_id},
        )
        task = asyncio.create_task(
            _import_duplicate(org_id, user_id, employee_id, document_id, _profile())
        )
        await asyncio.sleep(1.0)
        await holder.commit()

    item = await asyncio.wait_for(task, timeout=10)
    assert item.status == "approved", item.error_message
    employee = await _employee_row(employee_id)
    assert employee is not None
    assert employee.current_title == "Principal Engineer"
    assert employee.email == "morgan.vale@example.test"


async def test_the_listing_carries_the_merges_own_change_list():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    item_id, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    async with UnitOfWork() as uow:
        item = await uow.knowledge.get_import_item(item_id)
    assert item is not None
    [(_, target)] = await knowledge_service.list_import_items_with_targets(item.batch_id)
    assert target is not None and target.changes is not None
    assert [(c.field, c.on_file, c.from_resume, c.changes) for c in target.changes] == [
        ("email", None, "morgan.vale@example.test", True),
        ("current_title", "Engineer", "Principal Engineer", True),
        ("professional_location", "Pune", None, False),
        ("years_experience", None, 12.0, True),
    ]
    # Exactly what approving writes.
    await knowledge_service.approve_import_item(
        item_id, org_id, user_id, target_fingerprint=target.fingerprint
    )
    [merge] = await _audit(employee_id, "employee_resume_merge")
    assert merge.event_metadata["fields_changed"] == sorted(
        c.field for c in target.changes if c.changes
    )


async def test_a_payload_that_is_not_a_profile_lists_no_changes_and_cannot_be_approved():
    org_id, user_id = await _org()
    employee_id, _ = await _employee(org_id, user_id, verified=True)
    broken, _, _ = await _review_item(org_id, user_id, employee_id, _profile())
    async with UnitOfWork() as uow:
        item = await uow.knowledge.update_import_item(broken, extracted_payload={"name": NAME})
        # A second, readable item in the same batch still lists normally.
        readable = await uow.knowledge.create_import_item(
            batch_id=item.batch_id,
            source_ref="other",
            extracted_payload=_profile().model_dump(),
            source_document_id=item.source_document_id,
        )
        await uow.knowledge.update_import_item(
            readable.id, status="needs_review", target_knowledge_item_id=employee_id
        )
        await uow.commit()

    listed = dict(
        (row.id, target)
        for row, target in await knowledge_service.list_import_items_with_targets(item.batch_id)
    )
    assert listed[broken] is not None and listed[broken].changes is None
    assert listed[readable.id] is not None and listed[readable.id].changes is not None
    with pytest.raises(knowledge_service.ImportItemPayloadInvalidError):
        await knowledge_service.approve_import_item(
            broken, org_id, user_id, target_fingerprint=listed[broken].fingerprint
        )
    assert await _item_status(broken) == "needs_review"


async def test_the_automatic_branch_keeps_an_email_differing_only_in_case():
    org_id, user_id = await _org()
    employee_id, document_id = await _employee(
        org_id, user_id, verified=True, email="morgan.vale@example.test"
    )
    item = await _import_duplicate(
        org_id,
        user_id,
        employee_id,
        document_id,
        _profile(email=" Morgan.Vale@Example.TEST", current_title="Engineer"),
    )
    assert item.status == "approved", item.error_message
    employee = await _employee_row(employee_id)
    assert employee is not None and employee.email == "morgan.vale@example.test"


# ------------------------------------------------------- the listing's reads


async def _listing_statements(batch_id: uuid.UUID) -> tuple[list, int]:
    statements: list[str] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        rows = await knowledge_service.list_import_items_with_targets(batch_id)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count)
    return rows, len(statements)


async def _named_item(batch_id: uuid.UUID, target_id: uuid.UUID, email: str | None) -> uuid.UUID:
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch_id,
            source_ref=f"resume-{uuid.uuid4().hex}.pdf",
            extracted_payload=_profile(email=email).model_dump(),
        )
        await uow.knowledge.update_import_item(
            item.id, status="needs_review", target_knowledge_item_id=target_id
        )
        await uow.commit()
    return item.id


async def test_the_listing_reads_every_target_at_once_whatever_the_batch_size():
    """Plan item 80 (PR 33): it read each item's knowledge item on its own
    (one statement per item; measured 2,003 for 2,000 items). Now the
    statement count does not grow with the batch: the items, the batch, the
    organization's employees and every target's knowledge item, four in all.
    And the held-email rule reads as before: another employee's email is
    held, the employee's own (in any case) is not."""
    org_id, user_id = await _org()
    jordan, _ = await _employee(
        org_id, user_id, verified=True, email="jordan@example.test", name="Jordan Lee"
    )
    priya, _ = await _employee(
        org_id, user_id, verified=True, email="priya@example.test", name="Priya Nair"
    )
    batch_id = await _batch(org_id, user_id)
    takes_jordans = await _named_item(batch_id, priya, "jordan@example.test")
    keeps_own = await _named_item(batch_id, jordan, " JORDAN@example.test")

    rows, small = await _listing_statements(batch_id)
    emails = {
        item.id: next(f for f in target.changes if f.field == "email")
        for item, target in rows
        if target is not None and target.changes is not None
    }
    assert (emails[takes_jordans].held_by_other, emails[takes_jordans].changes) == (True, False)
    assert emails[keeps_own].held_by_other is False

    for _ in range(5):
        extra, _ = await _employee(org_id, user_id, verified=True, name=uuid.uuid4().hex)
        await _named_item(batch_id, extra, None)
    rows, large = await _listing_statements(batch_id)
    assert len(rows) == 7
    assert all(target is not None for _, target in rows)
    assert small == large == 4
