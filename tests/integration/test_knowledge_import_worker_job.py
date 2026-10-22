"""apps/worker/src/tenderos_worker/jobs/knowledge_import.py's
process_knowledge_document_import - had zero test coverage anywhere,
at any level, before this file. First real exercise of this function
against real content was a live browser upload against the full Docker
stack, not a test - it crashed on a plain employee-master.csv (see
KNOWN_LIMITATIONS.md for the full mechanism: this function called
parse_and_embed_document unconditionally for every batch type, but only
resumes/capability-statements actually need document_chunks; employee-
master/certifications parse deterministically off raw bytes and never
touch chunks).
"""

import io
import uuid
from types import SimpleNamespace

import docx
import pytest
from openpyxl import Workbook
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service, document_service, knowledge_service
from tenderos_worker.jobs import knowledge_import
from tenderos_worker.jobs.knowledge_import import process_knowledge_document_import

pytestmark = pytest.mark.asyncio


async def _make_org_and_user() -> tuple[uuid.UUID, uuid.UUID]:
    # knowledge_import_batches.created_by is a real FK to users - a bare
    # uuid.uuid4() here hits ForeignKeyViolationError on every caller
    # (the exact mistake tests/route/test_knowledge_routes.py's own
    # _seed_needs_review_item helper made once already this session -
    # made a second time drafting this file's first version, fixed the
    # same way: a real registered user via auth_service.register, not a
    # bare uuid).
    registered = await auth_service.register(
        email=f"{uuid.uuid4()}@example.com",
        password="hunter2",
        organization_name="Knowledge Import Job Test Org",
    )
    return registered.organization.id, registered.user.id


async def test_employee_master_csv_import_creates_real_items_without_ingestion():
    org_id, user_id = await _make_org_and_user()
    csv_bytes = (
        b"Employee Code,Name,Role,Status\r\n"
        b"EMP-01,Dana Whitfield,Senior Engineer,active\r\n"
        b"EMP-02,Jordan Reyes,Project Manager,active\r\n"
    )
    document, _is_new = await document_service.create_document(
        tender_id=None,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="employees.csv",
        content=csv_bytes,
        mime_type="text/csv",
        side="company",
        doc_type="employee_master",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "employee_master_csv", document.id, user_id
    )

    # Must not raise - this is the exact call that crashed with
    # "Unsupported document extension: '.csv'" before the fix.
    await process_knowledge_document_import(str(document.id), str(batch.id))

    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
        chunks = await uow.documents.list_chunks_for_document(document.id)
        employees = await uow.knowledge.list_knowledge_items_for_org(org_id, item_type="employee")

    assert len(items) == 2
    assert all(item.status == "approved" for item in items)
    assert len(employees) == 2
    # The real point of the fix, not just "it didn't crash": employee-
    # master import never needed generic ingestion, and now genuinely
    # doesn't run it - no document_chunks exist for this document at all.
    assert chunks == []


async def test_certification_documents_import_builds_its_own_chunk_without_generic_ingestion():
    # Certifications need exactly one synthetic chunk per certification
    # (import_certification_row's own _create_synthetic_chunk) - proving
    # generic ingestion is skipped here specifically means chunk COUNT
    # matches "one per certification," not "however many a generic
    # spreadsheet chunker would have produced."
    org_id, user_id = await _make_org_and_user()
    async with UnitOfWork() as uow:
        await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Dana Whitfield",
            content_fingerprint=uuid.uuid4().hex,
            title="Dana Whitfield",
            employee_code="EMP-01",
        )
        await uow.commit()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Certifications"
    sheet["A1"] = "Cert Key"
    sheet["B1"] = "Employee"
    sheet["C1"] = "Certification"
    sheet["D1"] = "Issuer"
    sheet["E1"] = "Issue Date"
    sheet["F1"] = "Expiry Date"
    sheet["A2"] = "CERT-01"
    sheet["B2"] = "EMP-01"
    sheet["C2"] = "Certified Scrum Master"
    sheet["D2"] = "Scrum Alliance"
    sheet["E2"] = "2024-01-01"
    sheet["F2"] = "2027-01-01"
    buf = io.BytesIO()
    workbook.save(buf)

    document, _is_new = await document_service.create_document(
        tender_id=None,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="certifications.xlsx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        side="company",
        doc_type="certification_documents",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "certification_documents", document.id, user_id
    )

    await process_knowledge_document_import(str(document.id), str(batch.id))

    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
        chunks = await uow.documents.list_chunks_for_document(document.id)

    assert len(items) == 1
    assert items[0].status == "approved"
    assert items[0].target_knowledge_item_id is not None
    # Exactly one synthetic chunk (import_certification_row's own), not
    # whatever a generic XLSX-as-pricing-sheet chunker would produce.
    assert len(chunks) == 1


async def test_a_duplicate_capability_statement_delivery_makes_no_llm_call_and_no_rows(
    monkeypatch,
):
    """Phase 4 consumer guard. The first delivery extracts (the two LLM
    entry points are replaced by counting fakes), creates one past project
    and one capability, and stamps the batch's completed_at. A second
    delivery of the same event stops before the LLM: no extraction call,
    no new import items, no new knowledge items. Before the guard it would
    repeat the extraction and then fail on uq_knowledge_import_item_source_ref."""
    org_id, user_id = await _make_org_and_user()
    doc = docx.Document()
    doc.add_paragraph("We delivered the Harbour Bridge retrofit for the Port Authority in 2022.")
    doc.add_paragraph("Capabilities: structural assessment and seismic retrofit.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _is_new = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename="capability-statement.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="capability_statement",
    )
    batch = await knowledge_service.create_import_batch(org_id, "manual", document.id, user_id)

    calls = {"extract": 0, "demonstrates": 0}

    async def fake_extract(client, model, chunks, organization_id):
        calls["extract"] += 1
        chunk = chunks[0]
        return SimpleNamespace(
            past_projects=[
                knowledge_service.ResolvedPastProject(
                    name="Harbour Bridge retrofit",
                    client="Port Authority",
                    year="2022",
                    description="Seismic retrofit of a harbour bridge.",
                    source_ref="p1",
                    chunk_id=chunk.id,
                    document_id=document.id,
                )
            ],
            capabilities=[
                knowledge_service.ResolvedCapability(
                    name="Seismic retrofit",
                    description="Structural assessment and seismic retrofit.",
                    source_ref="c1",
                    chunk_id=chunk.id,
                    document_id=document.id,
                )
            ],
        )

    async def fake_demonstrates(*args, **kwargs):
        calls["demonstrates"] += 1
        return []

    monkeypatch.setattr(knowledge_import, "extract_capability_statement", fake_extract)
    monkeypatch.setattr(knowledge_import, "extract_demonstrates", fake_demonstrates)

    async def counts():
        async with UnitOfWork() as uow:
            items = await uow.knowledge.list_import_items_for_batch(batch.id)
            projects = await uow.knowledge.list_knowledge_items_for_org(
                org_id, item_type="past_project"
            )
            capabilities = await uow.knowledge.list_knowledge_items_for_org(
                org_id, item_type="capability"
            )
            stamped = (await uow.knowledge.get_import_batch(batch.id)).completed_at
        return len(items), len(projects), len(capabilities), stamped

    await process_knowledge_document_import(str(document.id), str(batch.id))
    after_first = await counts()
    assert after_first[:3] == (2, 1, 1)
    assert after_first[3] is not None
    assert calls == {"extract": 1, "demonstrates": 1}

    await process_knowledge_document_import(str(document.id), str(batch.id))
    assert await counts() == after_first
    assert calls == {"extract": 1, "demonstrates": 1}
