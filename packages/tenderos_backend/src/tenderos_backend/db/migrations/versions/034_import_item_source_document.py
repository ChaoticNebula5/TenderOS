"""knowledge_import_items.source_document_id: the resume an item came from

Phase 4 plan item 80 (user decision 2026-11-18, option C). A resume that
matches an employee without settling who it is becomes a needs_review import
item. Approving it is meant to merge the resume into that employee, but the
item never recorded which document it came from: a bulk resume batch has no
source_document_id (one batch holds many resumes), and the item kept only the
file name. So approval had nothing to merge from, and merged nothing
(observed on the fake stack).

import_resume writes this column on every resume item. The other importers
leave it null: their rows come from one spreadsheet, the batch's own
source_document_id. Approval reads the resume's chunks through it.

ON DELETE SET NULL, as the other document references on knowledge rows; no
code deletes documents (read in code). An item whose document is gone, and
every resume item written before this column, is refused at approval with
IMPORT_ITEM_SOURCE_MISSING (upload the resume again). No backfill: a bulk
batch's items name a file, not a document, and two files in one upload can
share a name.

Downgrade drops the column.

Revision ID: 034_import_item_source_document
Revises: 033_document_chunk_extracted_by
Create Date: 2026-11-18 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "034_import_item_source_document"
down_revision: str | None = "033_document_chunk_extracted_by"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_import_items",
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "documents.id", ondelete="SET NULL", name="fk_import_item_source_document"
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("knowledge_import_items", "source_document_id")
