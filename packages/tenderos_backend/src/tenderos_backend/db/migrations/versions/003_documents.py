"""documents, document_ingestion_runs, document_chunks

Revision ID: 003_documents
Revises: 002_workflow
Create Date: 2026-08-06 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "003_documents"
down_revision: str | None = "002_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 001_initial.py ran only pgcrypto - nothing needed VECTOR until this
    # block's document_chunks.embedding column. Part C's own DDL lists this
    # extension immediately after pgcrypto, so this is the deferred half of
    # that same statement, not a new decision.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("side", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parse_status", sa.Text(), nullable=False, server_default="uploaded"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "supersedes_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("side IN ('client', 'company')", name="ck_document_side"),
        sa.CheckConstraint(
            "parse_status IN ('uploaded','queued','parsing','parsed','failed')",
            name="ck_document_parse_status",
        ),
        sa.CheckConstraint(
            "(tender_id IS NOT NULL AND organization_id IS NULL) "
            "OR (tender_id IS NULL AND organization_id IS NOT NULL)",
            name="ck_document_scope",
        ),
    )

    op.create_index(
        "uq_tender_document_hash",
        "documents",
        ["tender_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("tender_id IS NOT NULL"),
    )
    op.create_index(
        "uq_org_document_hash",
        "documents",
        ["organization_id", "sha256"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )

    op.create_table(
        "document_ingestion_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','completed','failed')",
            name="ck_document_ingestion_run_status",
        ),
        sa.UniqueConstraint("document_id", "parser_version", name="uq_document_ingestion_run"),
    )

    op.create_table(
        "document_chunks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_num", sa.Integer(), nullable=True),
        sa.Column("sheet_name", sa.Text(), nullable=True),
        sa.Column("cell_range", sa.Text(), nullable=True),
        sa.Column("heading_path", postgresql.JSONB(), nullable=True),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("bbox_json", postgresql.JSONB(), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_document_chunk_hash"),
    )

    op.create_index(
        "idx_document_chunks_document", "document_chunks", ["document_id", "chunk_index"]
    )
    op.execute(
        "CREATE INDEX idx_document_chunks_fts ON document_chunks "
        "USING GIN (to_tsvector('english', normalized_text))"
    )
    op.execute(
        "CREATE INDEX idx_document_chunks_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("document_chunks")
    op.drop_table("document_ingestion_runs")
    op.drop_index("uq_org_document_hash", table_name="documents")
    op.drop_index("uq_tender_document_hash", table_name="documents")
    op.drop_table("documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
