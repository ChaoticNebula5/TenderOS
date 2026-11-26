"""Plan item 80 (PR 18), the worker side of a resume review:
- an import that leaves its item in review extracts no WORKED_ON (before,
  the proposed employee got relationships no one had confirmed);
- an approval's ResumeMergeApproved job runs that extraction for the
  employee, from the item's resume, and does nothing for an item no longer
  approved;
- two different resumes with one file name in one upload are two items
  (before, the second claimed the first one's item and was dropped);
- the event's re-drive check.
The two model calls are replaced by recording fakes. Real Postgres. Every
fixture value is fake.
"""

import uuid
from types import SimpleNamespace

import pytest
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_KNOWLEDGE_IMPORT_ITEM,
    EVENT_RESUME_MERGE_APPROVED,
    resume_merge_approved_payload,
)
from tenderos_backend.domain.services import (
    auth_service,
    document_service,
    knowledge_service,
    outbox_redrive,
)
from tenderos_worker.jobs import knowledge_import

pytestmark = pytest.mark.asyncio


async def _org() -> tuple[uuid.UUID, uuid.UUID]:
    registered = await auth_service.register(
        email=f"{uuid.uuid4()}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Resume Merge Worker Org",
    )
    return registered.organization.id, registered.user.id


async def _resume(org_id: uuid.UUID, user_id: uuid.UUID, filename: str = "CV.docx"):
    label = uuid.uuid4().hex
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=filename,
        content=f"placeholder:{label}".encode(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="resume",
    )
    async with UnitOfWork() as uow:
        await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text=f"Resume {label}",
            normalized_text=f"resume {label}",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
    return document


class _Model:
    """Stands in for both model calls: the profile each resume yields, and a
    record of every WORKED_ON extraction (its chunks' document)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.names: list[str] = []
        self.worked_on: list[uuid.UUID] = []
        monkeypatch.setattr(
            knowledge_import, "_build_llm_client", lambda _organization_id: SimpleNamespace()
        )
        monkeypatch.setattr(knowledge_import, "extract_employee_profile", self._profile)
        monkeypatch.setattr(knowledge_import, "extract_worked_on", self._worked_on)

    async def _profile(self, client, model, chunks, organization_id):
        return ExtractedEmployeeProfile(name=self.names.pop(0), confidence=0.9)

    async def _worked_on(self, client, model, chunks, candidates, organization_id):
        self.worked_on.append(chunks[0].document_id)
        return []


async def test_an_import_left_in_review_extracts_no_worked_on(monkeypatch):
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    batch = await knowledge_service.create_import_batch(org_id, "bulk_resumes", None, user_id)

    first = await _resume(org_id, user_id, "first.docx")
    model.names.append("Jordan Pike")
    await knowledge_import._process_resume(first.id, batch.id, org_id, first.filename)
    assert model.worked_on == [first.id], "a NEW employee still gets WORKED_ON"

    # The same name with no strong signal: a review item naming Jordan Pike.
    second = await _resume(org_id, user_id, "second.docx")
    model.names.append("Jordan Pike")
    await knowledge_import._process_resume(second.id, batch.id, org_id, second.filename)

    items = await knowledge_service.list_import_items(batch.id)
    review = next(i for i in items if i.source_document_id == second.id)
    assert review.status == "needs_review"
    assert review.target_knowledge_item_id is not None
    assert model.worked_on == [first.id], "nothing extracted for the item in review"


async def test_two_resumes_with_one_file_name_in_one_upload_are_two_items(monkeypatch):
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    batch = await knowledge_service.create_import_batch(org_id, "bulk_resumes", None, user_id)
    first = await _resume(org_id, user_id, "CV.docx")
    second = await _resume(org_id, user_id, "CV.docx")
    assert first.id != second.id

    for document, name in ((first, "Casey Rowe"), (second, "Riley Stone")):
        model.names.append(name)
        await knowledge_import._process_resume(document.id, batch.id, org_id, document.filename)

    items = await knowledge_service.list_import_items(batch.id)
    assert sorted(i.source_document_id for i in items) == sorted([first.id, second.id])
    assert {i.source_ref for i in items} == {f"{first.id}/CV.docx", f"{second.id}/CV.docx"}
    assert all(i.status == "approved" for i in items)


async def test_a_retried_resume_job_resumes_its_own_item(monkeypatch):
    """The key change keeps a retry on the same document converging on the
    same item (the claim step's job), not a second one."""
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    batch = await knowledge_service.create_import_batch(org_id, "bulk_resumes", None, user_id)
    document = await _resume(org_id, user_id)
    model.names.extend(["Sam Hale", "Sam Hale"])
    await knowledge_import._process_resume(document.id, batch.id, org_id, document.filename)
    await knowledge_import._process_resume(document.id, batch.id, org_id, document.filename)
    assert len(await knowledge_service.list_import_items(batch.id)) == 1


async def _approved_merge(org_id, user_id, model) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(item_id, employee_id, resume document id) of an approved merge."""
    batch = await knowledge_service.create_import_batch(org_id, "bulk_resumes", None, user_id)
    first = await _resume(org_id, user_id, "a.docx")
    second = await _resume(org_id, user_id, "b.docx")
    model.names.extend(["Taylor Moss", "Taylor Moss"])
    await knowledge_import._process_resume(first.id, batch.id, org_id, first.filename)
    await knowledge_import._process_resume(second.id, batch.id, org_id, second.filename)
    items = await knowledge_service.list_import_items(batch.id)
    review = next(i for i in items if i.source_document_id == second.id)
    assert review.target_knowledge_item_id is not None
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(review.target_knowledge_item_id)
    assert employee is not None
    await knowledge_service.approve_import_item(
        review.id,
        org_id,
        user_id,
        target_fingerprint=knowledge_service.resume_target_fingerprint(employee),
    )
    return review.id, review.target_knowledge_item_id, second.id


async def test_the_approval_job_extracts_worked_on_from_the_approved_resume(monkeypatch):
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    item_id, employee_id, document_id = await _approved_merge(org_id, user_id, model)
    assert len(model.worked_on) == 1, "only the first (NEW) import extracted"

    calls: list[uuid.UUID] = []
    original = knowledge_import._import_worked_on

    async def recording(client, org, employee, chunks, profile):
        calls.append(employee)
        await original(client, org, employee, chunks, profile)

    monkeypatch.setattr(knowledge_import, "_import_worked_on", recording)
    await knowledge_import.process_resume_merge_approved(str(item_id))

    assert calls == [employee_id]
    assert model.worked_on[-1] == document_id


async def test_the_approval_job_does_nothing_for_an_item_no_longer_approved(monkeypatch):
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    item_id, _employee_id, _ = await _approved_merge(org_id, user_id, model)
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item_id, status="rejected")
        await uow.commit()
    before = list(model.worked_on)
    await knowledge_import.process_resume_merge_approved(str(item_id))
    assert model.worked_on == before
    await knowledge_import.process_resume_merge_approved(str(uuid.uuid4()))
    assert model.worked_on == before


async def test_the_redrive_check_allows_an_approved_item_only(monkeypatch):
    org_id, user_id = await _org()
    model = _Model(monkeypatch)
    item_id, _employee_id, _ = await _approved_merge(org_id, user_id, model)
    event = OutboxEvent(
        id=uuid.uuid4(),
        aggregate_type=AGGREGATE_KNOWLEDGE_IMPORT_ITEM,
        aggregate_id=item_id,
        event_type=EVENT_RESUME_MERGE_APPROVED,
        payload=resume_merge_approved_payload(item_id),
        idempotency_key=uuid.uuid4().hex,
    )
    async with UnitOfWork() as uow:
        allowed, _line = await outbox_redrive._check_resume_merge(uow, event, None)  # type: ignore[arg-type]
    assert allowed
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(item_id, status="rejected")
        await uow.commit()
    async with UnitOfWork() as uow:
        allowed, line = await outbox_redrive._check_resume_merge(uow, event, None)  # type: ignore[arg-type]
    assert not allowed and "rejected" in line
