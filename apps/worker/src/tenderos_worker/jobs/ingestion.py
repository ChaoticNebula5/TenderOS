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
    # Clear-then-reinsert within the caller's transaction (not committed
    # until parse_and_embed_document does) - safe under retry because
    # "stable chunking" guarantees the same chunk_index sequence for
    # unchanged source content, and a delete+insert of the same key within
    # one transaction never trips document_chunks' UNIQUE constraints.
    existing_chunks = await uow.documents.list_chunks_for_document(document_id)
    if existing_chunks:
        await uow.documents.delete_chunks_for_document(document_id)

    for chunk_index, (draft, embedding) in enumerate(zip(drafts, embeddings, strict=True)):
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


async def parse_and_embed_document(document_id: str) -> None:
    """Enqueued on DocumentUploaded (see outbox_dispatcher.py's routing
    table) - the ingestion queue's job: parse, OCR fallback, chunk, embed,
    per R1's queue table. Never runs inside the upload request itself.
    """
    doc_id = uuid.UUID(document_id)

    async with UnitOfWork() as uow:
        document = await uow.documents.get(doc_id)
        if document is None or document.tender_id is None:
            raise ValueError(f"Document {doc_id} not found or not tender-scoped")

        run = await uow.documents.get_ingestion_run(doc_id, PARSER_VERSION)
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
