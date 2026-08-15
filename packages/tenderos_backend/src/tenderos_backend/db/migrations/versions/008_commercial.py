"""commercial: pricing_documents, pricing_line_items, pricing_findings

Revision ID: 008_commercial
Revises: 007_talent
Create Date: 2026-08-14 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008_commercial"
down_revision: str | None = "007_talent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1417-1466 - transcribed verbatim.
    op.create_table(
        "pricing_documents",
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
            nullable=False,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sheet_name", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.UniqueConstraint("document_id", "sheet_name", name="uq_pricing_document_sheet"),
    )

    op.create_table(
        "pricing_line_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "pricing_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pricing_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cell_ref", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("raw_value", sa.Text(), nullable=True),
        sa.Column("numeric_value", sa.Numeric(), nullable=True),
        sa.Column("formula", sa.Text(), nullable=True),
        sa.Column("currency", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "pricing_document_id", "cell_ref", name="uq_pricing_line_item_cell"
        ),
        sa.UniqueConstraint(
            "pricing_document_id", "content_fingerprint", name="uq_pricing_line_item_fingerprint"
        ),
    )

    op.create_table(
        "pricing_findings",
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
            nullable=False,
        ),
        sa.Column(
            "finding_type",
            sa.Text(),
            nullable=False,
        ),
        sa.Column("item_a_ref", postgresql.JSONB(), nullable=True),
        sa.Column("item_b_ref", postgresql.JSONB(), nullable=True),
        sa.Column("delta", sa.Numeric(), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False, server_default=sa.text("'major'")),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "finding_type IN ("
            "'missing_cell','formula_error','wrong_total','currency_mismatch',"
            "'duplicate_cost','cross_doc_mismatch','unpriced_requirement',"
            "'needs_human_review'"
            ")",
            name="ck_pricing_finding_type",
        ),
        sa.CheckConstraint(
            "severity IN ('critical','major','minor')",
            name="ck_pricing_finding_severity",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_pricing_finding_fingerprint"
        ),
    )


def downgrade() -> None:
    op.drop_table("pricing_findings")
    op.drop_table("pricing_line_items")
    op.drop_table("pricing_documents")
