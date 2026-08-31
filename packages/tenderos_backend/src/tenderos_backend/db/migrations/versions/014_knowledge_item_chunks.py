"""knowledge_item_chunks: many-to-many employee<->chunk association, backfilled

Revision ID: 014_knowledge_item_chunks
Revises: 013_readiness
Create Date: 2026-08-31 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "014_knowledge_item_chunks"
down_revision: str | None = "013_readiness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # knowledge_items.source_chunk_id (a scalar FK) stays exactly as-is for
    # certifications/past_projects/capabilities - each is genuinely sourced
    # from one chunk, and that column is proven correct for them (see
    # docs/eval/citation-correctness-investigation.md). An employee's résumé
    # chunks into several sections (summary, experience, certifications,
    # education - B4's chunker), so no single FK column can represent "this
    # employee is evidenced by N chunks." This table is purely additive: a
    # second, many-to-many path alongside the untouched scalar column, not a
    # replacement for it.
    op.create_table(
        "knowledge_item_chunks",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )

    # Backfill for every employee that already exists: resume_document_id is
    # already correct (import_resume has always set it) even though no chunk
    # was ever associated - so this is real, derivable data, not a guess.
    # Every chunk of the employee's own résumé document becomes associated;
    # ON CONFLICT DO NOTHING makes this safe to re-run (idempotent), matching
    # this project's usual migration posture.
    op.execute(
        """
        INSERT INTO knowledge_item_chunks (knowledge_item_id, chunk_id)
        SELECT e.knowledge_item_id, dc.id
        FROM employees e
        JOIN document_chunks dc ON dc.document_id = e.resume_document_id
        WHERE e.resume_document_id IS NOT NULL
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("knowledge_item_chunks")
