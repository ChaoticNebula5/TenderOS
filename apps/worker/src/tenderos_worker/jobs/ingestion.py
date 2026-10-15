import logging
import uuid

from tenderos_backend.db.storage import storage
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.ingestion.chunk import ChunkDraft, ExtractedBlock, build_chunks
from tenderos_backend.ingestion.embed import embed_texts
from tenderos_backend.ingestion.parse_docx import parse_docx
from tenderos_backend.ingestion.parse_pdf import parse_pdf
from tenderos_backend.ingestion.parse_xlsx import parse_xlsx

logger = logging.getLogger(__name__)

# Bumping this creates a new document_ingestion_runs row (its UNIQUE is
# (document_id, parser_version)) rather than colliding with a prior run's
# history - the same "no real signal to version against yet" reasoning B3
# used for _STUB_PROMPT_BUNDLE_VERSION.
PARSER_VERSION = "b4-v1"


def _extract_blocks(filename: str, content: bytes) -> tuple[list[ExtractedBlock], int | None]:
    extension = filename[filename.rfind(".") :].lower() if "." in filename else ""
    if extension == ".pdf":
        blocks, page_count = parse_pdf(content)
        return blocks, page_count
    if extension == ".docx":
        return parse_docx(content), None
    if extension == ".xlsx":
        return parse_xlsx(content), None
    raise ValueError(f"Unsupported document extension: {extension!r}")


async def _persist_chunks(
    uow: UnitOfWork, document_id: uuid.UUID, drafts: list[ChunkDraft], embeddings: list[list[float]]
) -> None:
    """Major finding #9's real fix: reconcile by content_hash instead of a
    blanket delete-then-reinsert. The old shape deleted every existing
    chunk unconditionally, then recreated them all with fresh ids - safe
    for the chunks THEMSELVES (delete+insert of the same key never trips
    document_chunks' own UNIQUE constraints, within one transaction), but
    real, reachable damage for every OTHER table that cites a chunk by id:
    `Requirement.source_chunk_id` has no `ondelete` at all (NOT NULL, so
    Postgres blocks the delete outright with a real FK violation -
    reachable via the ALREADY-BUILT, already-tested POST /documents/{id}/
    reprocess route, no guard anywhere stops reprocessing a document that
    already has real extracted requirements); `KnowledgeItem`/
    `EvidenceLink.source_chunk_id` use `SET NULL`, so THEIR reprocessing
    silently succeeds while quietly severing every citation - two
    different failure modes, confirmed by reading every FK to document_
    chunks directly, not assumed from the entry's own original framing.

    document_chunks already carries a UNIQUE (document_id, content_hash)
    constraint - a chunk whose content_hash still matches a fresh
    re-parse IS the same chunk, by this project's own existing identity
    rule, not a new one that happens to look similar. Reusing its row (via
    update_chunk, same id) rather than deleting and reinserting means
    every FK pointing at it - the NOT-NULL-blocked ones and the
    SET-NULL-silenced ones alike - is simply never disturbed for
    unchanged content, which is the overwhelmingly common real trigger
    for reprocessing (a retry after a transient parse failure, not a
    genuinely edited source document). Only chunks whose content is
    genuinely NEW get inserted; only chunks whose content is genuinely
    GONE get deleted - and a delete at that point is correct, not a
    regression: a citation pointing at content no longer in the source
    document SHOULD go stale (blocked for Requirement, nulled for
    KnowledgeItem/EvidenceLink) exactly as it does today, since the fact
    it cited may no longer be true. Real content changes that still hit
    the Requirement block are a narrower, legitimate failure now, not
    fixed further here - a stale requirement citation needs a human to
    look at it, which is a real, separate re-extraction-cascade question,
    not something this fix silently papers over.
    """
    existing_by_hash = {
        chunk.content_hash: chunk
        for chunk in await uow.documents.list_chunks_for_document(document_id)
    }
    reused_ids: set[uuid.UUID] = set()

    for chunk_index, (draft, embedding) in enumerate(zip(drafts, embeddings, strict=True)):
        existing = existing_by_hash.get(draft.content_hash)
        if existing is not None:
            reused_ids.add(existing.id)
            await uow.documents.update_chunk(
                existing.id,
                chunk_index=chunk_index,
                chunk_text=draft.chunk_text,
                normalized_text=draft.normalized_text,
                page_num=draft.page_num,
                sheet_name=draft.sheet_name,
                cell_range=draft.cell_range,
                heading_path=draft.heading_path,
                token_count=draft.token_count,
                embedding=embedding,
            )
        else:
            await uow.documents.create_chunk(
                document_id=document_id,
                chunk_index=chunk_index,
                chunk_text=draft.chunk_text,
                normalized_text=draft.normalized_text,
                content_hash=draft.content_hash,
                page_num=draft.page_num,
                sheet_name=draft.sheet_name,
                cell_range=draft.cell_range,
                heading_path=draft.heading_path,
                token_count=draft.token_count,
                embedding=embedding,
            )

    stale_ids = [
        chunk.id for chunk in existing_by_hash.values() if chunk.id not in reused_ids
    ]
    await uow.documents.delete_chunks(stale_ids)


async def parse_and_embed_document(document_id: str) -> None:
    """Enqueued on DocumentUploaded (see outbox_dispatcher.py's routing
    table) - the ingestion queue's job: parse, OCR fallback, chunk, embed,
    per R1's queue table. Never runs inside the upload request itself.
    """
    doc_id = uuid.UUID(document_id)

    async with UnitOfWork() as uow:
        document = await uow.documents.get(doc_id)
        # B4 only ever uploaded tender-scoped documents, so this guard
        # originally required tender_id. B6 adds organization-scoped
        # documents (resumes, certs, employee-master, capability statement)
        # via ck_document_scope's tender_id-XOR-organization_id row shape -
        # parse/chunk/embed below only ever touch document_id, so the guard
        # just needs *a* scope, not specifically a tender.
        if document is None or (document.tender_id is None and document.organization_id is None):
            raise ValueError(f"Document {doc_id} not found or not scoped to a tender/organization")

        run = await uow.documents.get_ingestion_run(doc_id, PARSER_VERSION)
        # Phase 4 consumer guard. The outbox delivers DocumentUploaded at
        # least once (and the knowledge-import job calls this too). A run
        # for this document and parser version that already completed means
        # the chunks and embeddings exist: a second pass would only flip the
        # status back to "parsing" and re-embed every chunk for nothing. A
        # failed or interrupted run is not "completed", so it still re-runs.
        if run is not None and run.status == "completed":
            logger.info(
                "document %s already ingested with parser %s; duplicate delivery, skipping",
                doc_id,
                PARSER_VERSION,
            )
            return
        if run is None:
            run = await uow.documents.create_ingestion_run(doc_id, PARSER_VERSION)
        await uow.documents.mark_ingestion_run_status(run.id, "running")
        await uow.documents.update_parse_status(doc_id, "parsing")
        await uow.commit()

    try:
        content = await storage.get(document.storage_key)
        blocks, page_count = _extract_blocks(document.filename, content)
        drafts = build_chunks(blocks)
        embeddings = await embed_texts([draft.normalized_text for draft in drafts])

        async with UnitOfWork() as uow:
            await _persist_chunks(uow, doc_id, drafts, embeddings)
            await uow.documents.update_parse_status(doc_id, "parsed", page_count=page_count)
            await uow.documents.mark_ingestion_run_status(run.id, "completed")
            await uow.commit()

        logger.info("ingestion completed for document %s (%d chunks)", doc_id, len(drafts))

    except Exception as exc:
        # A5.1: never log document text/content, only IDs and the error's
        # own message (which describes the failure, not the document).
        logger.exception("ingestion failed for document %s", doc_id)
        async with UnitOfWork() as uow:
            await uow.documents.update_parse_status(doc_id, "failed")
            await uow.documents.mark_ingestion_run_status(
                run.id, "failed", error_code=type(exc).__name__, error_message=str(exc)
            )
            await uow.commit()
        raise
