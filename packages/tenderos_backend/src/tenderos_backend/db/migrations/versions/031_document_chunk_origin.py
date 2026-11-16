"""document_chunks.origin: a re-parse reconciles only the chunks it made

Phase 4 plan item 83 (user decision 2026-11-15, option A). The app has two
callers that add chunks to an already-uploaded document whose text was never
in the file: pricing grounding (a resolved pricing finding's note, on the
pricing workbook) and certification import (one row's text, on the
certifications workbook). The ingestion job's re-parse reconciled every chunk of the
document against the new parse and deleted those it did not produce, so a
re-parse of a pricing workbook with a resolved finding deleted that
finding's chunk; the evidence link's SET NULL then broke
ck_evidence_link_has_source, the parse failed, and it failed again on every
retry, leaving the bid unable to start a run (observed on the fake stack).

Upgrade, in one transaction:
1. origin TEXT NOT NULL DEFAULT 'parsed', checked to 'parsed' or
   'synthetic'.
2. Backfill 'synthetic' for every chunk cited by an evidence link with no
   knowledge item, or by a certification's source_chunk_id. Those are the
   citation shapes of the app's two callers, and each holds by construction:
   the only writer of a link with no knowledge item is pricing grounding,
   and every certification has cited the chunk created with it, each in the
   same transaction as its chunk (read in code and in git history). The
   backfill recognises only those two shapes, so a synthetic chunk made some
   other way before this migration stays 'parsed'. On an organization
   document that is harmless: nothing re-parses one (reprocess_document
   refuses a document with no tender), and retrieval reads both origins.
   From this migration on, create_synthetic_chunk stores every new synthetic
   chunk as 'synthetic', whoever calls it.
3. uq_document_chunk_index and uq_document_chunk_hash gain origin: each
   origin numbers and dedupes its own chunks.

Downgrade refuses, with a message, when a document has a parsed and a
synthetic chunk with the same content_hash, since the old key cannot hold
both. Otherwise it renumbers each document's synthetic chunks after its
highest parsed index, keeping their order (a synthetic chunk's index has no
meaning beyond uniqueness), restores the old keys and drops the column.

Revision ID: 031_document_chunk_origin
Revises: 030_readiness_report_content
Create Date: 2026-11-15 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "031_document_chunk_origin"
down_revision: str | None = "030_readiness_report_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column("origin", sa.Text(), nullable=False, server_default="parsed"),
    )
    op.create_check_constraint(
        "ck_document_chunk_origin", "document_chunks", "origin IN ('parsed', 'synthetic')"
    )
    op.execute(
        "UPDATE document_chunks SET origin = 'synthetic' WHERE id IN ("
        " SELECT source_chunk_id FROM evidence_links"
        " WHERE knowledge_item_id IS NULL AND source_chunk_id IS NOT NULL"
        " UNION"
        " SELECT source_chunk_id FROM knowledge_items"
        " WHERE item_type = 'certification' AND source_chunk_id IS NOT NULL"
        ")"
    )
    op.drop_constraint("uq_document_chunk_index", "document_chunks", type_="unique")
    op.drop_constraint("uq_document_chunk_hash", "document_chunks", type_="unique")
    op.create_unique_constraint(
        "uq_document_chunk_index", "document_chunks", ["document_id", "origin", "chunk_index"]
    )
    op.create_unique_constraint(
        "uq_document_chunk_hash", "document_chunks", ["document_id", "origin", "content_hash"]
    )


def downgrade() -> None:
    clash = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM document_chunks p JOIN document_chunks s"
                " ON s.document_id = p.document_id AND s.content_hash = p.content_hash"
                " WHERE p.origin = 'parsed' AND s.origin = 'synthetic'"
            )
        )
        .scalar_one()
    )
    if clash:
        raise RuntimeError(
            f"cannot downgrade 031: {clash} synthetic chunk(s) share a content_hash with a "
            "parsed chunk of the same document, which uq_document_chunk_hash "
            "(document_id, content_hash) cannot hold"
        )
    op.drop_constraint("uq_document_chunk_index", "document_chunks", type_="unique")
    op.drop_constraint("uq_document_chunk_hash", "document_chunks", type_="unique")
    op.execute(
        "UPDATE document_chunks c SET chunk_index = n.new_index FROM ("
        " SELECT s.id, COALESCE(("
        "   SELECT max(p.chunk_index) FROM document_chunks p"
        "   WHERE p.document_id = s.document_id AND p.origin = 'parsed'"
        " ), -1) + row_number() OVER (PARTITION BY s.document_id ORDER BY s.chunk_index)"
        " AS new_index"
        " FROM document_chunks s WHERE s.origin = 'synthetic'"
        ") n WHERE c.id = n.id"
    )
    op.create_unique_constraint(
        "uq_document_chunk_index", "document_chunks", ["document_id", "chunk_index"]
    )
    op.create_unique_constraint(
        "uq_document_chunk_hash", "document_chunks", ["document_id", "content_hash"]
    )
    op.drop_constraint("ck_document_chunk_origin", "document_chunks", type_="check")
    op.drop_column("document_chunks", "origin")
