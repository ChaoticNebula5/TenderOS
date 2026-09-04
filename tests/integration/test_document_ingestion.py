"""Upload -> outbox -> ingestion job, against real Postgres and MinIO.

Covers B4's smoke-test list and its do-not-proceed conditions: upload returns
without parsing, duplicate upload is safe, chunks keep page/sheet references,
embeddings are populated, and retries never duplicate chunks.
"""

import io
import uuid
import zipfile

import docx
import pytest
from openpyxl import Workbook
from sqlalchemy import select
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Ingestion Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Ingestion Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_heading("Section 1: Eligibility", level=1)
    document.add_paragraph("Bidders must hold a valid tax clearance certificate.")
    document.add_heading("Section 2: Insurance", level=1)
    document.add_paragraph("Professional indemnity cover of at least one million is required.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pricing"
    sheet["A1"], sheet["B1"] = "Item", "Cost"
    sheet["A2"], sheet["B2"] = "Labour", 1000
    sheet["A3"], sheet["B3"] = "Materials", 2000
    second = workbook.create_sheet("Schedule")
    second["A1"], second["B1"] = "Milestone", "Week"
    second["A2"], second["B2"] = "Mobilisation", 2
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


async def test_upload_creates_document_and_outbox_event_without_parsing():
    """G2 returns 202 with the document merely stored - parse_status is still
    'uploaded' and no chunks exist until the ingestion job runs."""
    org_id, tender_id = await _make_tender()

    document, is_new = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert is_new is True
    assert document.parse_status == "uploaded"
    assert document.sha256

    async with UnitOfWork() as uow:
        assert await uow.documents.list_chunks_for_document(document.id) == []

    async with get_session() as session:
        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == document.id)
        )
        events = result.scalars().all()
    assert [e.event_type for e in events] == ["DocumentUploaded"]


async def test_organization_scoped_upload_fires_no_generic_document_uploaded_event():
    """B13: create_document's own DocumentUploaded firing used to be
    unconditional, regardless of caller - an organization-scoped upload
    (tender_id=None; employee-master, certifications, resumes, capability
    statements, all real routes/knowledge.py callers) fired it exactly
    the same as a real tender document, and outbox_dispatcher always
    routes that event type to parse_and_embed_document - the generic
    tender-RAG pipeline, which has no idea what to do with a company
    knowledge document (crashes outright on .csv; for .xlsx/.docx it
    would silently create document_chunks/embeddings for a document with
    no tender_id at all). reprocess_document (same file) already refused
    to act when tender_id is None for the identical reason - create_
    document is the sibling call site that never adopted that guard.
    Caught live: uploading a real employee-master.csv through the actual
    browser against the full Docker stack, not by inspection - see
    KNOWN_LIMITATIONS.md.
    """
    org_id, _tender_id = await _make_tender()

    document, is_new = await document_service.create_document(
        tender_id=None,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="employees.csv",
        content=b"Name\r\nDana\r\n",
        mime_type="text/csv",
        side="company",
        doc_type="employee_master",
    )

    assert is_new is True

    async with get_session() as session:
        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == document.id)
        )
        events = result.scalars().all()
    assert events == [], (
        "an organization-scoped upload must not fire the tender-ingestion "
        "DocumentUploaded event at all"
    )


async def test_duplicate_upload_is_safe():
    """Same bytes, same tender - returns the existing row, writes no second
    document and no second ingestion event."""
    org_id, tender_id = await _make_tender()
    content = _docx_bytes()

    first, first_is_new = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="annexure.docx",
        content=content,
        mime_type=None,
    )
    second, second_is_new = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="annexure-renamed.docx",
        content=content,
        mime_type=None,
    )

    assert first_is_new is True
    assert second_is_new is False
    assert second.id == first.id

    async with UnitOfWork() as uow:
        assert len(await uow.documents.list_for_tender(tender_id)) == 1

    async with get_session() as session:
        events = (
            (await session.execute(select(OutboxEvent).where(OutboxEvent.aggregate_id == first.id)))
            .scalars()
            .all()
        )
    assert len(events) == 1, "a duplicate upload must not enqueue a second ingestion run"


async def test_ingestion_populates_chunks_with_provenance_and_embeddings():
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type=None,
    )

    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        refreshed = await uow.documents.get(document.id)
        chunks = await uow.documents.list_chunks_for_document(document.id)
        run = await uow.documents.get_ingestion_run(document.id, PARSER_VERSION)

    assert refreshed is not None and refreshed.parse_status == "parsed"
    assert run is not None and run.status == "completed"
    assert chunks, "ingestion produced no chunks"

    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.embedding is not None, "embeddings must be populated"
        assert len(chunk.embedding) == 384
        assert chunk.normalized_text
        assert chunk.token_count and chunk.token_count > 0

    # DOCX provenance is the heading stack, and it must actually be recorded.
    assert any(c.heading_path == ["Section 1: Eligibility"] for c in chunks), [
        c.heading_path for c in chunks
    ]
    assert any("tax clearance" in c.chunk_text for c in chunks)


async def test_xlsx_chunks_preserve_sheet_and_cell_range():
    """B4's do-not-proceed list: chunks must be able to return to their
    source sheet."""
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing.xlsx",
        content=_xlsx_bytes(),
        mime_type=None,
    )

    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)

    sheets = {c.sheet_name for c in chunks}
    assert sheets == {"Pricing", "Schedule"}, sheets
    assert all(c.cell_range for c in chunks), "cell_range must be recorded"
    # A chunk never mixes two sheets, so each sheet's content stays addressable.
    pricing = [c for c in chunks if c.sheet_name == "Pricing"]
    assert any("Labour" in c.chunk_text for c in pricing)
    assert not any("Mobilisation" in c.chunk_text for c in pricing)


async def test_reingestion_does_not_duplicate_chunks():
    """ "Retries duplicate chunks" is a named do-not-proceed condition. Running
    the job twice - a crash redelivery, or an explicit reprocess - must leave
    exactly the same chunk set."""
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type=None,
    )

    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        first = await uow.documents.list_chunks_for_document(document.id)
        first_hashes = [c.content_hash for c in first]

    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        second = await uow.documents.list_chunks_for_document(document.id)
        second_hashes = [c.content_hash for c in second]

    assert len(second) == len(first), "re-ingestion changed the chunk count"
    assert second_hashes == first_hashes, "stable chunking must reproduce the same hashes"
    assert [c.chunk_index for c in second] == list(range(len(second)))


async def test_reprocess_reuses_the_existing_ingestion_run_row():
    """document_ingestion_runs has UNIQUE(document_id, parser_version), so a
    second pass at the same parser version must update that row, not insert."""
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type=None,
    )

    await parse_and_embed_document(str(document.id))
    await parse_and_embed_document(str(document.id))

    async with get_session() as session:
        from tenderos_backend.db.models.document import DocumentIngestionRun

        runs = (
            (
                await session.execute(
                    select(DocumentIngestionRun).where(
                        DocumentIngestionRun.document_id == document.id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(runs) == 1
    assert runs[0].status == "completed"


async def test_corrupted_file_marks_document_failed():
    """The corrupted-file fixture must fail loudly and leave inspectable
    state (I4: failed jobs remain inspectable), never be treated as parsed."""
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="corrupted.docx",
        content=b"this is not a real docx file at all",
        mime_type=None,
    )

    # A .docx is a zip archive, so corruption surfaces as BadZipFile. The job
    # re-raises after recording the failure so RQ also marks it failed rather
    # than silently succeeding.
    with pytest.raises(zipfile.BadZipFile):
        await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        refreshed = await uow.documents.get(document.id)
        run = await uow.documents.get_ingestion_run(document.id, PARSER_VERSION)
        chunks = await uow.documents.list_chunks_for_document(document.id)

    assert refreshed is not None and refreshed.parse_status == "failed"
    assert run is not None and run.status == "failed"
    assert run.error_code, "the failure must be recorded for inspection"
    assert chunks == [], "a failed parse must not leave partial chunks"


async def test_reingestion_reuses_chunk_ids_for_unchanged_content():
    """Major finding #9's real fix: a re-parse of UNCHANGED content must
    reuse the same chunk rows (same ids), not delete and reinsert with
    fresh ones - the old shape's own real defect, silently invisible to
    test_reingestion_does_not_duplicate_chunks above (which only checks
    content_hash/chunk_index, never chunk identity itself)."""
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type=None,
    )

    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        first_ids = [c.id for c in await uow.documents.list_chunks_for_document(document.id)]

    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        second_ids = [c.id for c in await uow.documents.list_chunks_for_document(document.id)]

    assert set(second_ids) == set(first_ids), (
        "unchanged content must reuse the exact same chunk rows across re-ingestion, "
        "not delete-then-reinsert with fresh ids - this is what actually protects "
        "every FK pointing at a chunk (Requirement.source_chunk_id, KnowledgeItem."
        "source_chunk_id, EvidenceLink.source_chunk_id) from a reprocess"
    )


async def test_reprocessing_a_document_with_a_real_requirement_citation_no_longer_fails():
    """The exact reachable bug this finding named: Requirement.source_
    chunk_id has no `ondelete` at all (NOT NULL, so Postgres blocks a
    delete outright) - before this fix, re-ingesting a document that
    already had a real Requirement extracted from it would hit a genuine
    FK violation the moment _persist_chunks tried its own blanket
    delete-then-reinsert, reachable via the real, already-tested
    POST /documents/{id}/reprocess route (no guard anywhere stops it).
    Proven here at the job level directly - not the route - since the
    route itself only enqueues; parse_and_embed_document is where the
    real delete used to happen.
    """
    org_id, tender_id = await _make_tender()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="eligibility.docx",
        content=_docx_bytes(),
        mime_type=None,
    )
    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
        cited_chunk_id = chunks[0].id
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-001",
            requirement_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate.",
            category="eligibility",
            mandatory=True,
            source_chunk_id=cited_chunk_id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()

    # Before this fix: a real asyncpg.exceptions.ForeignKeyViolationError
    # here, from the blanket delete_chunks_for_document call - the same
    # document content, reprocessed, with a real citation in place.
    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        refreshed_document = await uow.documents.get(document.id)
        refreshed_requirement = await uow.requirements.get(requirement.id)

    assert refreshed_document is not None and refreshed_document.parse_status == "parsed", (
        "reprocessing a document with a real requirement citation must succeed, "
        "not fail on a chunk-delete FK violation"
    )
    assert refreshed_requirement is not None
    assert refreshed_requirement.source_chunk_id == cited_chunk_id, (
        "the requirement's own citation must still point at a real, valid chunk "
        "after reprocessing unchanged content - not nulled, not dangling"
    )
