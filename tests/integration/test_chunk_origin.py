"""Plan item 83: a re-parse reconciles only the chunks the parser made.

Two callers add a "synthetic" chunk to an already-uploaded document, whose
text was never in the file: pricing grounding (a resolved finding's note, on
the pricing workbook) and certification import (one row, on the
certifications workbook). Before this item the ingestion job's reconcile
deleted every chunk the new parse did not produce, synthetic ones included.
On the fake stack (observed) a reprocess of a pricing workbook with a
resolved finding then failed on ck_evidence_link_has_source, failed again on
every retry, and left the bid unable to start a run. A certification's chunk
would have gone silently (SET NULL), had anything re-parsed its workbook.

Covered here, against real Postgres and S3, through the real ingestion job:
- a pricing workbook's re-parse keeps a resolved finding's chunk and link,
  and moves no state version (the graph shows no chunk; plan item 95);
- a certifications workbook's re-parse keeps the certification's chunk;
- a re-parse that yields more chunks than before passes a synthetic index;
- a new synthetic chunk takes a fresh index after the parsed chunks shrink;
- requirement extraction's chunk list holds no synthetic chunk;
- a workbook the bug left failed parses on Retry, and the bid can run.
"""

import io
import uuid
from dataclasses import replace

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from tenderos_backend.db.models.document import (
    CHUNK_ORIGIN_PARSED,
    CHUNK_ORIGIN_SYNTHETIC,
    DocumentChunk,
    DocumentIngestionRun,
)
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    auth_service,
    commercial_service,
    document_service,
    knowledge_service,
)
from tenderos_worker.jobs import ingestion as ingestion_job
from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document
from tenderos_worker.jobs.knowledge_import import process_knowledge_document_import

pytestmark = pytest.mark.asyncio

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


async def _org_user_tender() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    registered = await auth_service.register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name=f"Chunk Origin {uuid.uuid4().hex[:8]}",
    )
    org_id, user_id = registered.organization.id, registered.user.id
    async with get_session() as session:
        tender = Tender(organization_id=org_id, name="Chunk Origin Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org_id, user_id, tender.id


def _pricing_workbook() -> bytes:
    # Two sheets, so the first parse makes at least two parsed chunks.
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pricing"
    sheet["A1"], sheet["B1"], sheet["C1"] = "Item", "Unit Cost", "Quantity"
    sheet["A2"], sheet["B2"], sheet["C2"] = "Senior Engineer", 120, 180
    sheet["A3"], sheet["B3"], sheet["C3"] = "Site Survey", 8500, 1
    second = workbook.create_sheet("Summary")
    second["A1"], second["B1"] = "Grand Total", 30100
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


async def _parsed_pricing_workbook(
    org_id: uuid.UUID, user_id: uuid.UUID, tender_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    """An uploaded, parsed pricing workbook and its pricing_documents row:
    (document id, pricing document id)."""
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=user_id,
        filename="pricing_schedule.xlsx",
        content=_pricing_workbook(),
        mime_type=_XLSX,
        doc_type="pricing_workbook",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        pricing_document = await uow.commercial.create_pricing_document(
            tender_id=tender_id, document_id=document.id, sheet_name="Pricing"
        )
        await uow.commit()
    return document.id, pricing_document.id


async def _resolved_finding(
    tender_id: uuid.UUID, pricing_document_id: uuid.UUID, user_id: uuid.UUID, note: str
) -> tuple[uuid.UUID, uuid.UUID]:
    """Creates and resolves one pricing finding through the service, as the
    route does: (evidence link id, its synthetic chunk id)."""
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note=note,
            content_fingerprint=uuid.uuid4().hex,
            pricing_document_id=pricing_document_id,
        )
        await uow.commit()
    await commercial_service.resolve_pricing_finding(finding.id, user_id)
    fingerprint = commercial_service.pricing_finding_evidence_fingerprint(finding.id)
    async with UnitOfWork() as uow:
        link = await uow.compliance.get_evidence_link_by_fingerprint(tender_id, fingerprint)
    assert link is not None and link.source_chunk_id is not None
    return link.id, link.source_chunk_id


async def _reprocess(org_id: uuid.UUID, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """POST /documents/{id}/reprocess's service call, then the job it
    enqueues (the route only enqueues; the job is where chunks change)."""
    await document_service.reprocess_document(document_id, org_id, user_id)
    await parse_and_embed_document(str(document_id))


async def _run_status(document_id: uuid.UUID) -> str:
    async with get_session() as session:
        run = (
            await session.execute(
                select(DocumentIngestionRun).where(
                    DocumentIngestionRun.document_id == document_id,
                    DocumentIngestionRun.parser_version == PARSER_VERSION,
                )
            )
        ).scalar_one()
        return run.status


async def _chunk(chunk_id: uuid.UUID) -> DocumentChunk | None:
    async with UnitOfWork() as uow:
        return await uow.documents.get_chunk(chunk_id)


async def test_a_reparse_of_a_pricing_workbook_keeps_a_resolved_findings_chunk_and_link():
    org_id, user_id, tender_id = await _org_user_tender()
    document_id, pricing_document_id = await _parsed_pricing_workbook(org_id, user_id, tender_id)
    link_id, chunk_id = await _resolved_finding(
        tender_id, pricing_document_id, user_id, "Senior Engineer's line total is 21600.0."
    )
    async with UnitOfWork() as uow:
        parsed_before = [c.id for c in await uow.documents.list_chunks_for_document(document_id)]
        version_before = (await uow.tenders.get(tender_id)).state_version

    await _reprocess(org_id, user_id, document_id)

    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
        link = await uow.compliance.get_evidence_link(link_id)
        parsed_after = [c.id for c in await uow.documents.list_chunks_for_document(document_id)]
        version_after = (await uow.tenders.get(tender_id)).state_version
    chunk = await _chunk(chunk_id)

    assert document is not None and document.parse_status == "parsed"
    assert await _run_status(document_id) == "completed"
    assert link is not None and link.source_chunk_id == chunk_id, "the link keeps its chunk"
    assert chunk is not None and chunk.origin == CHUNK_ORIGIN_SYNTHETIC
    assert parsed_after == parsed_before, "an unchanged file reuses every parsed chunk"
    # Nothing the projector writes changed: no chunk is a graph node, and an
    # Evidence node carries no chunk id (plan item 95's rule: no bump).
    assert version_after == version_before


async def test_a_reparse_of_a_certifications_workbook_keeps_the_certifications_chunk():
    # Nothing re-parses an organization document today (reprocess refuses
    # one, and a certification import never calls the parser); this calls
    # the job directly, as any future path would, so the chunk's safety no
    # longer rests on that.
    org_id, user_id, _tender_id = await _org_user_tender()
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
    for column, value in zip(
        "ABCDEF",
        ["Cert Key", "Employee", "Certification", "Issuer", "Issue Date", "Expiry Date"],
        strict=True,
    ):
        sheet[f"{column}1"] = value
    for column, value in zip(
        "ABCDEF",
        [
            "CERT-01",
            "EMP-01",
            "Certified Scrum Master",
            "Scrum Alliance",
            "2024-01-01",
            "2027-01-01",
        ],
        strict=True,
    ):
        sheet[f"{column}2"] = value
    buf = io.BytesIO()
    workbook.save(buf)
    document, _ = await document_service.create_document(
        tender_id=None,
        organization_id=org_id,
        actor_user_id=user_id,
        filename="certifications.xlsx",
        content=buf.getvalue(),
        mime_type=_XLSX,
        side="company",
        doc_type="certification_documents",
    )
    batch = await knowledge_service.create_import_batch(
        org_id, "certification_documents", document.id, user_id
    )
    await process_knowledge_document_import(str(document.id), str(batch.id))
    async with UnitOfWork() as uow:
        items = await uow.knowledge.list_import_items_for_batch(batch.id)
        certification = await uow.knowledge.get_knowledge_item(items[0].target_knowledge_item_id)
    assert certification is not None and certification.source_chunk_id is not None
    chunk_id = certification.source_chunk_id

    await parse_and_embed_document(str(document.id))

    async with UnitOfWork() as uow:
        refreshed = await uow.knowledge.get_knowledge_item(certification.id)
        parsed = await uow.documents.list_chunks_for_document(document.id)
    chunk = await _chunk(chunk_id)
    assert parsed, "the parser made chunks of its own from the workbook"
    assert refreshed is not None and refreshed.source_chunk_id == chunk_id
    assert chunk is not None and chunk.origin == CHUNK_ORIGIN_SYNTHETIC


async def test_a_reparse_that_yields_more_chunks_passes_a_synthetic_chunks_index(monkeypatch):
    org_id, user_id, tender_id = await _org_user_tender()
    document_id, pricing_document_id = await _parsed_pricing_workbook(org_id, user_id, tender_id)
    _link_id, chunk_id = await _resolved_finding(
        tender_id, pricing_document_id, user_id, "Site Survey is priced twice."
    )
    async with UnitOfWork() as uow:
        parsed_count = len(await uow.documents.list_chunks_for_document(document_id))
    synthetic = await _chunk(chunk_id)
    assert synthetic is not None and synthetic.chunk_index == parsed_count, (
        "precondition: the synthetic chunk sits just after the parsed ones"
    )

    real_build_chunks = ingestion_job.build_chunks

    def three_more_chunks(blocks):
        # A parser that now yields three more chunks: the first lands on the
        # index the synthetic chunk holds, the others pass it.
        drafts = real_build_chunks(blocks)
        extras = [f"Extra row {n} a newer parser finds." for n in range(3)]
        return [
            *drafts,
            *(
                replace(drafts[-1], chunk_text=e, normalized_text=e, content_hash=uuid.uuid4().hex)
                for e in extras
            ),
        ]

    monkeypatch.setattr(ingestion_job, "build_chunks", three_more_chunks)
    await _reprocess(org_id, user_id, document_id)

    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
        parsed = await uow.documents.list_chunks_for_document(document_id)
    assert document is not None and document.parse_status == "parsed"
    assert [c.chunk_index for c in parsed] == list(range(parsed_count + 3))
    assert (await _chunk(chunk_id)) is not None

    # The next synthetic chunk numbers from the highest synthetic index
    # (the user's rule), not from the parsed chunks that now pass it.
    _link, next_chunk_id = await _resolved_finding(
        tender_id, pricing_document_id, user_id, "Senior Engineer is listed twice."
    )
    next_chunk = await _chunk(next_chunk_id)
    assert next_chunk is not None and next_chunk.chunk_index == parsed_count + 1


async def test_a_new_synthetic_chunk_takes_a_fresh_index_after_the_parsed_chunks_shrink(
    monkeypatch,
):
    org_id, user_id, tender_id = await _org_user_tender()
    document_id, pricing_document_id = await _parsed_pricing_workbook(org_id, user_id, tender_id)
    async with UnitOfWork() as uow:
        parsed_count = len(await uow.documents.list_chunks_for_document(document_id))
    assert parsed_count >= 2, "precondition: the workbook parses into two or more chunks"
    _a, first = await _resolved_finding(tender_id, pricing_document_id, user_id, "Finding A.")
    _b, second = await _resolved_finding(tender_id, pricing_document_id, user_id, "Finding B.")

    real_build_chunks = ingestion_job.build_chunks
    monkeypatch.setattr(ingestion_job, "build_chunks", lambda blocks: real_build_chunks(blocks)[:1])
    await _reprocess(org_id, user_id, document_id)
    monkeypatch.setattr(ingestion_job, "build_chunks", real_build_chunks)

    # One parsed chunk is left, and the synthetic ones still sit at
    # parsed_count and parsed_count + 1. A count of the document's chunks
    # (the old rule) or of its synthetic chunks would both reuse one.
    _c, third = await _resolved_finding(tender_id, pricing_document_id, user_id, "Finding C.")

    indexes = [(await _chunk(chunk_id)).chunk_index for chunk_id in (first, second, third)]
    assert indexes == [parsed_count, parsed_count + 1, parsed_count + 2]


async def test_requirement_extractions_chunk_list_holds_no_synthetic_chunk():
    # requirement_extraction_node reads list_chunks_for_tender, its only
    # source of tender text; a finding's note must not reach the extractor.
    org_id, user_id, tender_id = await _org_user_tender()
    document_id, pricing_document_id = await _parsed_pricing_workbook(org_id, user_id, tender_id)
    _link_id, chunk_id = await _resolved_finding(
        tender_id, pricing_document_id, user_id, "Grand total is 30100, but the lines sum to 30200."
    )

    async with UnitOfWork() as uow:
        tender_chunks = await uow.documents.list_chunks_for_tender(tender_id)
        parsed = await uow.documents.list_chunks_for_document(document_id)

    assert chunk_id not in {c.id for c in tender_chunks}
    assert {c.id for c in tender_chunks} == {c.id for c in parsed}
    assert all(c.origin == CHUNK_ORIGIN_PARSED for c in tender_chunks)


async def test_a_workbook_the_bug_left_failed_parses_on_retry_and_the_bid_can_run():
    # The state the old reconcile left behind (observed on the fake stack):
    # the document and its ingestion run marked failed, chunks and links
    # untouched. Retry is the same reprocess call.
    org_id, user_id, tender_id = await _org_user_tender()
    document_id, pricing_document_id = await _parsed_pricing_workbook(org_id, user_id, tender_id)
    link_id, chunk_id = await _resolved_finding(
        tender_id, pricing_document_id, user_id, "Cross-Border Logistics is priced in EUR."
    )
    async with UnitOfWork() as uow:
        run = await uow.documents.get_ingestion_run(document_id, PARSER_VERSION)
        await uow.documents.update_parse_status(document_id, "failed")
        await uow.documents.mark_ingestion_run_status(
            run.id,
            "failed",
            error_code="IntegrityError",
            error_message="ck_evidence_link_has_source",
        )
        await uow.commit()
    async with UnitOfWork() as uow:
        assert await uow.documents.all_parsed_for_tender(tender_id) is False

    await _reprocess(org_id, user_id, document_id)

    async with UnitOfWork() as uow:
        document = await uow.documents.get(document_id)
        link = await uow.compliance.get_evidence_link(link_id)
        runnable = await uow.documents.all_parsed_for_tender(tender_id)
    assert document is not None and document.parse_status == "parsed"
    assert link is not None and link.source_chunk_id == chunk_id
    assert runnable is True, "every document parsed: a new run may start"
