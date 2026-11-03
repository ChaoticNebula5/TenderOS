import logging
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.document import Document, DocumentChunk, DocumentIngestionRun

logger = logging.getLogger(__name__)


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_tender_and_hash(self, tender_id: uuid.UUID, sha256: str) -> Document | None:
        result = await self._session.execute(
            select(Document).where(Document.tender_id == tender_id, Document.sha256 == sha256)
        )
        return result.scalar_one_or_none()

    async def get_by_org_and_hash(
        self, organization_id: uuid.UUID, sha256: str
    ) -> Document | None:
        # Org-scoped sibling of get_by_tender_and_hash above - B6's first
        # caller of the uq_org_document_hash index 003_documents.py already
        # created but nothing used until now (B4 only ever uploaded
        # tender-scoped documents).
        result = await self._session.execute(
            select(Document).where(
                Document.organization_id == organization_id, Document.sha256 == sha256
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        side: str,
        doc_type: str,
        filename: str,
        storage_key: str,
        sha256: str,
        mime_type: str | None,
        file_size_bytes: int,
        tender_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        version: int = 1,
        supersedes_document_id: uuid.UUID | None = None,
    ) -> Document:
        document = Document(
            tender_id=tender_id,
            organization_id=organization_id,
            side=side,
            doc_type=doc_type,
            filename=filename,
            storage_key=storage_key,
            sha256=sha256,
            mime_type=mime_type,
            file_size_bytes=file_size_bytes,
            version=version,
            supersedes_document_id=supersedes_document_id,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def get(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_for_tender(self, tender_id: uuid.UUID) -> list[Document]:
        result = await self._session.execute(
            select(Document).where(Document.tender_id == tender_id).order_by(Document.created_at)
        )
        return list(result.scalars().all())

    async def update_parse_status(
        self, document_id: uuid.UUID, status: str, page_count: int | None = None
    ) -> None:
        document = await self._session.get(Document, document_id)
        if document is None:
            raise ValueError(f"Document {document_id} not found")
        document.parse_status = status
        if page_count is not None:
            document.page_count = page_count
        await self._session.flush()

    async def get_ingestion_run(
        self, document_id: uuid.UUID, parser_version: str
    ) -> DocumentIngestionRun | None:
        result = await self._session.execute(
            select(DocumentIngestionRun).where(
                DocumentIngestionRun.document_id == document_id,
                DocumentIngestionRun.parser_version == parser_version,
            )
        )
        return result.scalar_one_or_none()

    async def reopen_completed_ingestion_runs(self, document_id: uuid.UUID) -> int:
        """Phase 4: an explicit reprocess sets a document's completed
        ingestion runs back to 'queued', so parse_and_embed_document's
        consumer guard (skip a run that already completed) lets this
        deliberate re-run through while still skipping a duplicate delivery
        of the original upload. Returns how many runs were reopened."""
        result = await self._session.execute(
            update(DocumentIngestionRun)
            .where(
                DocumentIngestionRun.document_id == document_id,
                DocumentIngestionRun.status == "completed",
            )
            .values(status="queued")
            .returning(DocumentIngestionRun.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())

    async def create_ingestion_run(
        self, document_id: uuid.UUID, parser_version: str
    ) -> DocumentIngestionRun:
        run = DocumentIngestionRun(
            document_id=document_id, status="queued", parser_version=parser_version
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def mark_ingestion_run_status(
        self,
        run_id: uuid.UUID,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        run = await self._session.get(DocumentIngestionRun, run_id)
        if run is None:
            raise ValueError(f"DocumentIngestionRun {run_id} not found")
        run.status = status
        if status == "running" and run.started_at is None:
            run.started_at = datetime.now(UTC)
        if status in ("completed", "failed"):
            run.completed_at = datetime.now(UTC)
        run.error_code = error_code
        run.error_message = error_message
        await self._session.flush()

    async def list_chunks_for_document(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self._session.execute(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def get_chunk(self, chunk_id: uuid.UUID) -> DocumentChunk | None:
        return await self._session.get(DocumentChunk, chunk_id)

    async def list_chunks_for_tender(self, tender_id: uuid.UUID) -> list[DocumentChunk]:
        # Ordered by (document, chunk_index) so batch.py's provenance-respecting
        # batcher sees each document's chunks contiguously and in original
        # reading order - it groups consecutive same-provenance chunks, which
        # only means something if the input is already in that order.
        result = await self._session.execute(
            select(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(Document.tender_id == tender_id)
            .order_by(DocumentChunk.document_id, DocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def search_chunks_lexical_for_org(
        self, organization_id: uuid.UUID, query_text: str, limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        """Postgres full-text search over document_chunks.normalized_text,
        scoped to Document.organization_id (never Document.tender_id) - this
        is what keeps B6 retrieval from crossing organization boundaries,
        an explicit "do not proceed if" item. Uses the same
        to_tsvector('english', normalized_text) expression as
        idx_document_chunks_fts (003_documents.py) so Postgres can actually
        use that index rather than a sequential scan.
        """
        tsvector = sa.func.to_tsvector("english", DocumentChunk.normalized_text)
        tsquery = sa.func.plainto_tsquery("english", query_text)
        rank = sa.func.ts_rank(tsvector, tsquery)
        result = await self._session.execute(
            select(DocumentChunk, rank)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(Document.organization_id == organization_id, tsvector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit)
        )
        return [(row[0], row[1]) for row in result.all()]

    async def search_chunks_vector_for_org(
        self, organization_id: uuid.UUID, query_embedding: list[float], limit: int
    ) -> list[tuple[DocumentChunk, float]]:
        """pgvector cosine-distance search over document_chunks.embedding,
        same organization-boundary scoping as the lexical search above.
        Uses idx_document_chunks_embedding_hnsw (003_documents.py). Returns
        cosine distance (0 = identical, 2 = opposite) - the caller inverts
        it to a similarity score for fusion, not this method's concern.

        The HNSW index is shared by every organization, and pgvector applies
        the organization filter after the index scan: with the defaults it
        scans hnsw.ef_search (40) nearest chunks of any organization, so a
        small organization in a large table got few or none of its own
        (Phase 4 plan item 41, decision 0064). An iterative scan keeps going
        until LIMIT rows pass the filter, up to hnsw.max_scan_tuples
        (20,000 by default). relaxed_order may return rows slightly out of
        distance order; the caller fuses ranks (RRF) and reranks anyway.
        SET LOCAL ends with the transaction, so it cannot leak to a later
        query on a pooled connection.
        """
        await self._session.execute(sa.text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        result = await self._session.execute(
            select(DocumentChunk, distance)
            .join(Document, DocumentChunk.document_id == Document.id)
            .where(
                Document.organization_id == organization_id, DocumentChunk.embedding.isnot(None)
            )
            .order_by(distance)
            .limit(limit)
        )
        rows = [(row[0], row[1]) for row in result.all()]
        if len(rows) < limit:
            # Expected when the organization has fewer chunks than the
            # LIMIT; otherwise the scan cap was reached. Logged either way
            # so a shortfall is visible; never the query or its text.
            logger.info(
                "vector search for organization %s returned %d of %d rows",
                organization_id,
                len(rows),
                limit,
            )
        return rows

    async def all_parsed_for_tender(self, tender_id: uuid.UUID) -> bool:
        # all() on an empty list is True - guarded explicitly so a tender with
        # zero uploaded documents doesn't silently pass this check and start a
        # workflow run that extracts nothing and pauses at requirement_gate
        # with an empty, confusing "review" screen.
        documents = await self.list_for_tender(tender_id)
        return len(documents) > 0 and all(doc.parse_status == "parsed" for doc in documents)

    async def create_chunk(
        self,
        document_id: uuid.UUID,
        chunk_index: int,
        chunk_text: str,
        normalized_text: str,
        content_hash: str,
        page_num: int | None = None,
        sheet_name: str | None = None,
        cell_range: str | None = None,
        heading_path: list | None = None,
        token_count: int | None = None,
        embedding: list[float] | None = None,
    ) -> DocumentChunk:
        chunk = DocumentChunk(
            document_id=document_id,
            chunk_index=chunk_index,
            chunk_text=chunk_text,
            normalized_text=normalized_text,
            content_hash=content_hash,
            page_num=page_num,
            sheet_name=sheet_name,
            cell_range=cell_range,
            heading_path=heading_path,
            token_count=token_count,
            embedding=embedding,
        )
        self._session.add(chunk)
        await self._session.flush()
        return chunk

    async def update_chunk(
        self,
        chunk_id: uuid.UUID,
        chunk_index: int,
        chunk_text: str,
        normalized_text: str,
        page_num: int | None,
        sheet_name: str | None,
        cell_range: str | None,
        heading_path: list | None,
        token_count: int | None,
        embedding: list[float] | None,
    ) -> DocumentChunk:
        """Major finding #9's real fix - update an EXISTING chunk row in
        place (same id) rather than delete-then-reinsert, for a chunk
        whose content_hash still matches a fresh re-parse. Preserving the
        row's own id is what actually protects every FK pointing at it
        (Requirement.source_chunk_id has no ondelete at all, NOT NULL -
        a delete Postgres would outright block; KnowledgeItem/EvidenceLink
        use SET NULL, silently losing the citation) - a plain re-chunk of
        unchanged content no longer disturbs either. Every field except
        document_id/content_hash is still refreshed, not just chunk_index -
        the same underlying content_hash doesn't guarantee page_num/
        heading_path/embedding are unchanged too (pagination can shift for
        reasons unrelated to this one chunk's own text).
        """
        chunk = await self._session.get(DocumentChunk, chunk_id)
        if chunk is None:
            raise ValueError(f"DocumentChunk {chunk_id} not found")
        chunk.chunk_index = chunk_index
        chunk.chunk_text = chunk_text
        chunk.normalized_text = normalized_text
        chunk.page_num = page_num
        chunk.sheet_name = sheet_name
        chunk.cell_range = cell_range
        chunk.heading_path = heading_path
        chunk.token_count = token_count
        chunk.embedding = embedding
        await self._session.flush()
        return chunk

    async def delete_chunks(self, chunk_ids: list[uuid.UUID]) -> None:
        """Deletes specific chunks by id - major finding #9's fix uses this
        for only the chunks whose content is genuinely GONE from a fresh
        re-parse, not every chunk the document ever had (delete_chunks_
        for_document above is now dead - its one caller, _persist_chunks,
        is rewritten to use update_chunk/create_chunk/this method instead;
        removed rather than left as unused)."""
        if not chunk_ids:
            return
        for chunk_id in chunk_ids:
            chunk = await self._session.get(DocumentChunk, chunk_id)
            if chunk is not None:
                await self._session.delete(chunk)
        await self._session.flush()
