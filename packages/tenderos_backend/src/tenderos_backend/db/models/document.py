import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    UniqueConstraint,
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
    parse_status: Mapped[str] = mapped_column(nullable=False, default="uploaded")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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
