"""pricing_findings.pricing_document_id: a real provenance gap, not just grounding enablement

Revision ID: 015_pricing_finding_document
Revises: 014_knowledge_item_chunks
Create Date: 2026-09-02 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "015_pricing_finding_document"
down_revision: str | None = "014_knowledge_item_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Before this, a pricing_findings row carried no record of which
    # uploaded document it came from - not just missing for grounding
    # (B16 Group 6), a real provenance gap in its own right, in a system
    # whose entire thesis is traceability. item_a_ref/item_b_ref (Part C)
    # cover cross_doc_mismatch, which genuinely spans two documents; the
    # other 7 finding types (missing_cell, formula_error, wrong_total,
    # currency_mismatch, duplicate_cost, unpriced_requirement, needs_
    # human_review) had nothing at all. Nullable and stays NULL for
    # cross_doc_mismatch by design - that finding type's two documents
    # are already both named, symmetrically, via item_a_ref/item_b_ref;
    # a single FK here would have to arbitrarily privilege one side.
    op.add_column(
        "pricing_findings",
        sa.Column(
            "pricing_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pricing_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # No backfill: unlike knowledge_item_chunks (014), the source document
    # for an EXISTING single-document finding is genuinely not derivable
    # from anything already in Postgres. cell_refs (the only thing that
    # would disambiguate it) was only ever used to compute this row's own
    # content_fingerprint - a one-way hash, not reversible - and was never
    # itself persisted as a column. Every pre-existing pricing_findings
    # row stays NULL here permanently, not just until a future re-run:
    # evaluate_commercial_for_tender is idempotent by content_fingerprint
    # (get_pricing_finding_by_fingerprint short-circuits to the existing
    # row without updating it), so re-running commercial evaluation on an
    # already-ingested tender will never retroactively populate this
    # column either. A real, accepted limit - see KNOWN_LIMITATIONS.md.


def downgrade() -> None:
    op.drop_column("pricing_findings", "pricing_document_id")
