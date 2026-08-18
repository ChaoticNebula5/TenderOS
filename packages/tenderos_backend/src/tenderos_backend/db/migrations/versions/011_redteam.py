"""redteam: redteam_findings

Revision ID: 011_redteam
Revises: 010_graph
Create Date: 2026-08-17 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011_redteam"
down_revision: str | None = "010_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1559-1575 - transcribed verbatim. target_id is nullable
    # (unlike every other *_id FK in this schema) because some finding
    # categories (e.g. a missing mandatory submission item) have no single
    # proposal_claim/compliance_entry/etc. row to point at - target_type
    # alone still identifies what kind of thing failed.
    op.create_table(
        "redteam_findings",
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
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("finding_category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("finding_text", sa.Text(), nullable=False),
        sa.Column("rebuttal", sa.Text(), nullable=True),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("violated_condition", sa.Text(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "severity IN ('critical','major','minor')",
            name="ck_redteam_finding_severity",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_redteam_finding_fingerprint"
        ),
    )


def downgrade() -> None:
    op.drop_table("redteam_findings")
