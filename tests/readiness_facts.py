"""Saved facts readiness reads, set up the way a clean bid has them (plan
items 94, 96, 99 and 104).

Readiness now blocks a bid with no parsed document, a parsed document whose
text no completed extraction has read (or that has no text), and a bid on
which no complete red-team pass has seen the current state version. A test
that means "this bid is otherwise clean" sets these up here rather than
repeating the rows in each file:

- add_parsed_document: a tender document whose parse finished, with one
  parsed chunk that a completed extraction has read;
- mark_chunks_read: what a completed requirement extraction records, for
  every parsed chunk of the bid not yet read (a test that adds its own
  chunks calls it after adding them);
- record_complete_pass: a completed pass, graph checks run, at the bid's
  current version (what run_red_team records after a pass on a fresh graph);
- project_and_review: project the graph, then record that pass, which is
  what "projected and reviewed at this version" means.
"""

import uuid

from sqlalchemy import select, update
from tenderos_backend.db.models.document import CHUNK_ORIGIN_PARSED, Document, DocumentChunk
from tenderos_backend.db.models.workflow import WorkflowRun, WorkflowTask
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service


async def mark_chunks_read(tender_id: uuid.UUID) -> uuid.UUID:
    """A completed run with a completed requirement extraction task that read
    every parsed chunk of the bid not yet marked. The run is created
    completed, never active, so it cannot meet the one-active-run rule of a
    run the test makes itself. Returns the task's id."""
    async with get_session() as session:
        run_id = uuid.uuid4()
        session.add(
            WorkflowRun(
                id=run_id,
                tender_id=tender_id,
                langgraph_thread_id=str(run_id),
                status="completed",
                prompt_bundle_version="test",
                model_config_json={},
            )
        )
        await session.flush()
        task = WorkflowTask(
            workflow_run_id=run_id,
            task_key=f"requirement_extraction:{run_id}",
            task_type="requirement_extraction",
            assigned_agent="requirements",
            status="completed",
        )
        session.add(task)
        await session.flush()
        bid_documents = select(Document.id).where(Document.tender_id == tender_id)
        await session.execute(
            update(DocumentChunk)
            .where(
                DocumentChunk.document_id.in_(bid_documents),
                DocumentChunk.origin == CHUNK_ORIGIN_PARSED,
                DocumentChunk.extracted_by_task_id.is_(None),
            )
            .values(extracted_by_task_id=task.id)
        )
        await session.commit()
        return task.id


async def add_parsed_document(tender_id: uuid.UUID, filename: str = "rfp.pdf") -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename=filename,
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/pdf",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        await uow.documents.update_parse_status(document.id, "parsed")
        await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="The bidder shall comply.",
            normalized_text="the bidder shall comply.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
    await mark_chunks_read(tender_id)
    return document.id


async def record_complete_pass(tender_id: uuid.UUID) -> int:
    """A completed pass whose graph checks ran at the bid's current version.
    Returns that version."""
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(tender_id)
        assert tender is not None
        version = tender.state_version
        await uow.redteam.record_pass(
            tender_id, outcome="completed", graph_checks="ran", graph_state_version=version
        )
        await uow.commit()
        return version


async def project_and_review(tender_id: uuid.UUID) -> int:
    await graph_service.project_tender(tender_id)
    return await record_complete_pass(tender_id)
