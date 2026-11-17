"""document_chunks.extracted_by_task_id: which extraction read a chunk

Phase 4 plan item 104 (user decision 2026-11-17, option C). Requirement
extraction reads the bid's parsed chunks once per run, listed at the start of
its node. Nothing recorded what it read, and nothing refuses an upload once a
run has started, so a document added after the extraction (a tender
amendment, say) or new text from a re-parse had no requirements extracted,
and readiness could say ready while ignoring it.

This column names the requirement extraction task that read the chunk. The
node sets it, for exactly the chunks it listed, in the transaction that marks
its task completed, so a failed extraction marks nothing. The parse reconcile
keeps the row (and the mark) for unchanged text and inserts unmarked rows for
new text. Readiness blocks on a tender document's parsed chunks left unmarked.

ON DELETE SET NULL is never reached on its own (read in code): no code
deletes workflow_tasks or workflow_runs; tasks cascade from runs and runs
from tenders, and a deleted tender deletes these chunks as well.

The partial index holds only unmarked parsed chunks, the rows readiness's
query looks for, so it stays small.

No backfill (user decision, Q5): no record says what an earlier extraction
read, and guessing from times can claim a chunk was read when it was not.
Existing bids show the new blocker until their next run.

Downgrade drops the index and the column.

Revision ID: 033_document_chunk_extracted_by
Revises: 032_redteam_passes
Create Date: 2026-11-17 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "033_document_chunk_extracted_by"
down_revision: str | None = "032_redteam_passes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "extracted_by_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "idx_document_chunks_unextracted",
        "document_chunks",
        ["document_id"],
        postgresql_where=sa.text("extracted_by_task_id IS NULL AND origin = 'parsed'"),
    )


def downgrade() -> None:
    op.drop_index("idx_document_chunks_unextracted", table_name="document_chunks")
    op.drop_column("document_chunks", "extracted_by_task_id")
