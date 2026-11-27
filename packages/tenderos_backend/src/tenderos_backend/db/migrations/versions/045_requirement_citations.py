"""requirement_citations: every chunk a requirement was found in

Plan item 125 (user decisions 2026-11-27, Q2 a).

The chunker carries up to 150 characters of a chunk's trailing lines into
the next chunk (ingestion/chunk.py, OVERLAP_CHARS), so a short requirement
there is found whole in both, and extraction made two rows for it: its key
includes the chunk (observed in the real-provider run: the same 11 of 69
planted requirements doubled in every cold run). Extraction now recognises
the second copy and records its chunk here instead of making a row
(requirement_service.persist_extraction_results).

One row per (requirement, chunk), every citation included: the primary one,
which requirements.source_chunk_id still holds for verification, merge,
split and a re-parse's foreign key, and any found in an overlapping
neighbour. Both foreign keys cascade: a requirement's citations go with it,
and a citation goes with its chunk, so a re-parse that removes a chunk fails
on the primary citation only, as before (plan item 108), never on this
table.

Upgrade creates the table and its chunk index and copies every
requirement's source_chunk_id into it. Downgrade drops it; source_chunk_id
was never changed.

Revision ID: 045_requirement_citations
Revises: 044_readiness_report_generation
Create Date: 2026-11-27 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "045_requirement_citations"  # alembic_version holds 32 characters
down_revision: str | None = "044_readiness_report_generation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirement_citations",
        sa.Column("requirement_id", UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name="requirement_citations_requirement_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name="requirement_citations_chunk_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("requirement_id", "chunk_id", name="requirement_citations_pkey"),
    )
    op.create_index("idx_requirement_citations_chunk", "requirement_citations", ["chunk_id"])
    op.execute(
        "INSERT INTO requirement_citations (requirement_id, chunk_id) "
        "SELECT id, source_chunk_id FROM requirements"
    )


def downgrade() -> None:
    op.drop_index("idx_requirement_citations_chunk", table_name="requirement_citations")
    op.drop_table("requirement_citations")
