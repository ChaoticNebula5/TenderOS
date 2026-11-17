"""Plan item 104: which chunks a requirement extraction read, and readiness's
blocker for a document it never read.

Requirement extraction lists the bid's parsed chunks at the start of its node
and reads exactly those. Before this item nothing recorded that, and nothing
refuses an upload once a run has started, so a document added after the
extraction (a tender amendment, say), or new text from a re-parse, had no
requirements extracted, and readiness could say ready while ignoring it
(observed on the fake stack). Each parsed chunk now names the extraction
task that read it (document_chunks.extracted_by_task_id, migration 033),
set in the transaction that completes the task.

Covered here, through the real node (FakeLLMClient), the real ingestion job
and real Postgres:
- the writer: a completed extraction marks exactly the chunks it listed; a
  chunk a parse commits during the extraction stays unread; a failing batch
  and a failure after the requirements commit mark nothing;
- the reconcile: a re-parse of unchanged text keeps every mark, and a
  re-parse with one new chunk leaves exactly that one unread;
- readiness's blocker 10 (document_not_extracted): each message (none read,
  some read, an amendment, no readable text), doc_type choosing the wording
  only, an unparsed document left to blocker 9, synthetic chunks and
  organization documents never blocking, a new run clearing it, and one
  stored report for two generates with nothing changed.
"""

import io
import uuid
from dataclasses import replace

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.client import LLMError
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
)
from tenderos_backend.db.models.document import CHUNK_ORIGIN_PARSED, DocumentChunk
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, readiness_service
from tenderos_backend.orchestration.nodes import requirement_extraction
from tenderos_backend.orchestration.nodes.requirement_extraction import (
    requirement_extraction_node,
)
from tenderos_worker.jobs import ingestion as ingestion_job
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_NEW_RUN = (
    "Start a new run on the Upload page to extract its requirements. If a run is still open, "
    "first cancel it there or request changes at its gate."
)


async def _bid() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"Extraction Marks {uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Extraction Marks Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


def _docx(*paragraphs: str) -> bytes:
    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _upload(
    org_id: uuid.UUID, tender_id: uuid.UUID, filename: str, *paragraphs: str
) -> uuid.UUID:
    """Uploads a docx through the service and runs the ingestion job, as the
    route and the ingestion worker do."""
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=_docx(*paragraphs),
        mime_type=_DOCX,
    )
    await parse_and_embed_document(str(document.id))
    return document.id


async def _run_row(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()
        return run.id


async def _finish_run(run_id: uuid.UUID) -> None:
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run_id, "completed")
        await uow.commit()


def _state(tender_id: uuid.UUID, run_id: uuid.UUID) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": "unused-by-this-node",
        "workflow_run_id": str(run_id),
        "langgraph_thread_id": str(run_id),
        "status": "extracting_requirements",
        "plan_version": 0,
        "active_gate_id": None,
        "revision_count": 0,
        "max_revisions": 2,
        "retry_budget_remaining": 10,
        "dispatch_task_ids": [],
        "completed_task_ids": [],
        "failed_task_ids": [],
        "unverified_requirement_ids": [],
        "open_critical_finding_ids": [],
        "changed_knowledge_item_ids": [],
        "readiness_report_id": None,
        "graph_projection_version": None,
        "degraded_stages": [],
    }


def _client(*outputs: ExtractionOutput) -> FakeLLMClient:
    """One extraction answer per batch; every answer after the scripted ones
    extracts nothing. Requirements are optional, so no verification call."""
    client = FakeLLMClient()
    client.script("requirement_extraction", ExtractionOutput, list(outputs))
    client.set_fallback("requirement_extraction", ExtractionOutput, ExtractionOutput())
    return client


def _one_optional_requirement() -> ExtractionOutput:
    return ExtractionOutput(
        requirements=[
            ExtractedRequirement(
                requirement_text="The bidder should describe its quality plan.",
                category="technical",
                mandatory=False,
                source_chunk_ref="chunk_1",
                confidence=0.9,
            )
        ]
    )


async def _extract(monkeypatch, tender_id: uuid.UUID, client: FakeLLMClient) -> uuid.UUID:
    """Runs the real node for a new run, then ends the run so the next one
    can start. Returns the extraction task's id."""
    run_id = await _run_row(tender_id)
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: client)
    await requirement_extraction_node(_state(tender_id, run_id))
    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(run_id, f"requirement_extraction:{run_id}")
    await _finish_run(run_id)
    assert task is not None
    return task.id


async def _marks(document_id: uuid.UUID) -> list[uuid.UUID | None]:
    async with get_session() as session:
        rows = await session.execute(
            select(DocumentChunk.extracted_by_task_id)
            .where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.origin == CHUNK_ORIGIN_PARSED,
            )
            .order_by(DocumentChunk.chunk_index)
        )
        return list(rows.scalars().all())


async def _not_extracted(tender_id: uuid.UUID) -> list[tuple[str | None, str]]:
    result = await readiness_service.evaluate_readiness(tender_id)
    return [
        (b["target_id"], b["message"])
        for b in result.blockers
        if b["category"] == readiness_service.DOCUMENT_NOT_EXTRACTED
    ]


# ---------------------------------------------------------------- the writer


async def test_a_completed_extraction_marks_exactly_the_chunks_it_listed(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    assert await _marks(document_id) == [None], "precondition: parsed, not yet read"

    task_id = await _extract(monkeypatch, tender_id, _client())

    assert await _marks(document_id) == [task_id]
    assert await _not_extracted(tender_id) == []


async def test_a_chunk_parsed_during_the_extraction_stays_unread(monkeypatch):
    # The case a time comparison can miss: the second document's parse
    # commits after the node listed the chunks and before it completes.
    org_id, tender_id = await _bid()
    first = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    real_extract = requirement_extraction.extract_requirements
    late: list[uuid.UUID] = []

    async def parse_a_document_meanwhile(*args, **kwargs):
        late.append(
            await _upload(
                org_id, tender_id, "amendment_2.docx", "The bidder must sign an integrity pact."
            )
        )
        return await real_extract(*args, **kwargs)

    monkeypatch.setattr(requirement_extraction, "extract_requirements", parse_a_document_meanwhile)
    task_id = await _extract(monkeypatch, tender_id, _client())

    assert await _marks(first) == [task_id]
    assert await _marks(late[0]) == [None]
    assert await _not_extracted(tender_id) == [
        (
            str(late[0]),
            "Document 'amendment_2.docx' has not been read by requirement extraction. "
            + _NEW_RUN
            + " Then review the requirements it changes.",
        )
    ]


async def test_a_failing_batch_marks_nothing(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    client = FakeLLMClient()
    # Malformed twice: the first answer and its one repair attempt, so the
    # batch raises out of extract_requirements.
    client.script_malformed("requirement_extraction", ExtractionOutput)
    client.script_malformed("requirement_extraction", ExtractionOutput)
    run_id = await _run_row(tender_id)
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: client)

    with pytest.raises(LLMError):
        await requirement_extraction_node(_state(tender_id, run_id))

    assert await _marks(document_id) == [None]
    assert [doc for doc, _ in await _not_extracted(tender_id)] == [str(document_id)]


async def test_a_failure_after_the_requirements_commit_marks_nothing(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder should plan quality.")

    async def submission_items_fail(*_args, **_kwargs):
        raise RuntimeError("submission items failed")

    monkeypatch.setattr(requirement_extraction, "persist_submission_items", submission_items_fail)
    run_id = await _run_row(tender_id)
    monkeypatch.setattr(
        requirement_extraction, "_build_llm_client", lambda: _client(_one_optional_requirement())
    )

    with pytest.raises(RuntimeError, match="submission items failed"):
        await requirement_extraction_node(_state(tender_id, run_id))

    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)
    assert len(requirements) == 1, "precondition: the requirements committed"
    assert await _marks(document_id) == [None]


# ------------------------------------------------------------- the reconcile


async def _reprocess(org_id: uuid.UUID, document_id: uuid.UUID) -> None:
    """POST /documents/{id}/reprocess's service call, then the job it
    enqueues."""
    await document_service.reprocess_document(document_id, org_id, uuid.uuid4())
    await parse_and_embed_document(str(document_id))


async def test_a_reparse_of_unchanged_text_keeps_every_mark(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    task_id = await _extract(monkeypatch, tender_id, _client())

    await _reprocess(org_id, document_id)

    assert await _marks(document_id) == [task_id]
    assert await _not_extracted(tender_id) == []


async def test_a_reparse_with_one_new_chunk_leaves_exactly_that_one_unread(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    task_id = await _extract(monkeypatch, tender_id, _client())
    real_build_chunks = ingestion_job.build_chunks
    added = "The bidder must hold a quality certificate."

    def one_more_chunk(blocks):
        # A newer parser that finds one more passage in the same file.
        drafts = real_build_chunks(blocks)
        return [
            *drafts,
            replace(
                drafts[-1],
                chunk_text=added,
                normalized_text=added.lower(),
                content_hash=uuid.uuid4().hex,
            ),
        ]

    monkeypatch.setattr(ingestion_job, "build_chunks", one_more_chunk)
    await _reprocess(org_id, document_id)

    assert await _marks(document_id) == [task_id, None]
    assert await _not_extracted(tender_id) == [
        (
            str(document_id),
            "Document 'rfp.docx' changed after the last requirement extraction "
            "(1 of 2 parts not read). " + _NEW_RUN,
        )
    ]


# ---------------------------------------------------------- readiness blocker


async def test_a_document_no_extraction_read_blocks_whatever_its_doc_type(monkeypatch):
    # doc_type picks the wording only (Q4): a renamed amendment blocks too.
    org_id, tender_id = await _bid()
    rfp = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    await _extract(monkeypatch, tender_id, _client())
    notice = await _upload(org_id, tender_id, "notice.docx", "Clause 4 now asks for five projects.")
    amendment = await _upload(org_id, tender_id, "amendment_2.docx", "Clause 5 is withdrawn.")

    async with UnitOfWork() as uow:
        types = {d.id: d.doc_type for d in await uow.documents.list_for_tender(tender_id)}
    assert types[notice] != "amendment" and types[amendment] == "amendment", "precondition"
    assert sorted(await _not_extracted(tender_id)) == sorted(
        [
            (
                str(notice),
                "Document 'notice.docx' has not been read by requirement extraction. " + _NEW_RUN,
            ),
            (
                str(amendment),
                "Document 'amendment_2.docx' has not been read by requirement extraction. "
                + _NEW_RUN
                + " Then review the requirements it changes.",
            ),
        ]
    )
    result = await readiness_service.evaluate_readiness(tender_id)
    assert result.counts["documents_not_extracted"] == 2
    assert str(rfp) not in {b["target_id"] for b in result.blockers}


async def test_a_parsed_document_with_no_readable_text_blocks(monkeypatch):
    # Q6: a scan whose OCR found nothing looks exactly like an empty file, so
    # neither is ignored. The amendment sentence needs text to review.
    org_id, tender_id = await _bid()
    await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    await _extract(monkeypatch, tender_id, _client())
    blank = await _upload(org_id, tender_id, "addendum_3.docx")

    async with UnitOfWork() as uow:
        document = await uow.documents.get(blank)
    assert document is not None and document.parse_status == "parsed", "precondition"
    assert document.doc_type == "amendment", "precondition"
    assert await _marks(blank) == [], "precondition: no parsed chunks"
    assert await _not_extracted(tender_id) == [
        (
            str(blank),
            "Document 'addendum_3.docx' has no readable text, so no requirements could be "
            "extracted from it. Check the file; a scan may not have been read.",
        )
    ]


async def test_an_unparsed_document_gets_blocker_9_only():
    _org_id, tender_id = await _bid()
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.pdf",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        await uow.documents.update_parse_status(document.id, "failed")
        await uow.commit()

    result = await readiness_service.evaluate_readiness(tender_id)

    assert [b["category"] for b in result.blockers if b["target_id"] == str(document.id)] == [
        readiness_service.DOCUMENT_NOT_PARSED
    ]
    assert result.counts["documents_not_extracted"] == 0


async def test_synthetic_chunks_and_organization_documents_never_block(monkeypatch):
    org_id, tender_id = await _bid()
    document_id = await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    await _extract(monkeypatch, tender_id, _client())
    async with UnitOfWork() as uow:
        # A synthetic chunk added after the extraction (a resolved pricing
        # finding's note, say): not the file's text, never extracted.
        await uow.documents.create_chunk(
            document_id=document_id,
            chunk_index=await uow.documents.next_synthetic_chunk_index(document_id),
            chunk_text="Resolved: the total was corrected.",
            normalized_text="resolved: the total was corrected.",
            content_hash=uuid.uuid4().hex,
            origin="synthetic",
        )
        # An organization document (a resume, say) with an unread parsed
        # chunk: not part of the bid.
        org_document = await uow.documents.create(
            side="company",
            doc_type="resume",
            filename="resume.docx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type=_DOCX,
            file_size_bytes=10,
            organization_id=org_id,
        )
        await uow.documents.update_parse_status(org_document.id, "parsed")
        await uow.documents.create_chunk(
            document_id=org_document.id,
            chunk_index=0,
            chunk_text="Ten years of site work.",
            normalized_text="ten years of site work.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()

    assert await _not_extracted(tender_id) == []


async def test_a_new_run_clears_it(monkeypatch):
    org_id, tender_id = await _bid()
    await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")
    await _extract(monkeypatch, tender_id, _client())
    amendment = await _upload(org_id, tender_id, "amendment_2.docx", "Clause 5 is withdrawn.")
    assert [doc for doc, _ in await _not_extracted(tender_id)] == [str(amendment)]

    task_id = await _extract(monkeypatch, tender_id, _client())

    assert await _marks(amendment) == [task_id]
    assert await _not_extracted(tender_id) == []


async def test_two_generates_with_nothing_changed_store_one_report(monkeypatch):
    org_id, tender_id = await _bid()
    await _upload(org_id, tender_id, "rfp.docx", "The bidder must be registered.")

    first = await readiness_service.evaluate_readiness(tender_id)
    second = await readiness_service.evaluate_readiness(tender_id)

    assert first.counts["documents_not_extracted"] == 1, "precondition: the blocker is there"
    assert second.report_id == first.report_id
