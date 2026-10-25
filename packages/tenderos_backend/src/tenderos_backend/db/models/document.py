import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("side IN ('client', 'company')", name="ck_document_side"),
        CheckConstraint(
            "parse_status IN ('uploaded','queued','parsing','parsed','failed')",
            name="ck_document_parse_status",
        ),
        CheckConstraint(
            "(tender_id IS NOT NULL AND organization_id IS NULL) "
            "OR (tender_id IS NULL AND organization_id IS NOT NULL)",
            name="ck_document_scope",
        ),
        # Partial unique indexes, not UniqueConstraint - Postgres unique
        # constraints can't carry a WHERE clause, and these only apply to
        # one side of ck_document_scope's own XOR at a time (a tender-scoped
        # row has organization_id NULL, and vice versa - a plain UNIQUE
        # (tender_id, sha256) would let every org-scoped row's NULL
        # tender_id collide with every other one, since Postgres treats
        # NULL as distinct in a unique index only for non-partial ones in a
        # way that's easy to get backwards). Migration 003_documents.py
        # created these directly via op.create_index - this mirrors that
        # real schema so Base.metadata.create_all() (every pytest test
        # database) actually has them too. Found missing here, not assumed
        # present, while writing a regression test for the concurrent-
        # upload race (document_service.py) that depends on this exact
        # constraint firing - the test silently couldn't reproduce the race
        # at all under create_all()'s own (until now, incomplete) schema.
        Index(
            "uq_tender_document_hash",
            "tender_id",
            "sha256",
            unique=True,
            postgresql_where="tender_id IS NOT NULL",
        ),
        Index(
            "uq_org_document_hash",
            "organization_id",
            "sha256",
            unique=True,
            postgresql_where="organization_id IS NOT NULL",
        ),
    )

    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    side: Mapped[str] = mapped_column(nullable=False)
    doc_type: Mapped[str] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(nullable=False)
    storage_key: Mapped[str] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(nullable=False)
    mime_type: Mapped[str | None] = mapped_column(nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parse_status: Mapped[str] = mapped_column(
        nullable=False, default="uploaded", server_default="uploaded"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    supersedes_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )


class DocumentIngestionRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','completed','failed')",
            name="ck_document_ingestion_run_status",
        ),
        UniqueConstraint("document_id", "parser_version", name="uq_document_ingestion_run"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(nullable=False)
    parser_version: Mapped[str] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)


class DocumentChunk(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        UniqueConstraint("document_id", "content_hash", name="uq_document_chunk_hash"),
        # 003_documents.py. The full-text index is on the same expression
        # search_chunks_lexical_for_org queries, so the planner can use it.
        Index("idx_document_chunks_document", "document_id", "chunk_index"),
        Index(
            "idx_document_chunks_fts",
            text("to_tsvector('english', normalized_text)"),
            postgresql_using="gin",
        ),
        Index(
            "idx_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(nullable=True)
    cell_range: Mapped[str | None] = mapped_column(nullable=True)
    heading_path: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    chunk_text: Mapped[str] = mapped_column(nullable=False)
    normalized_text: Mapped[str] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bbox_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
