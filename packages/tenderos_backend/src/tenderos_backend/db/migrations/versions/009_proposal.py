"""proposal: proposal_sections, proposal_claims, proposal_claim_evidence,
proposal_claim_requirements, proposal_claim_employees

Revision ID: 009_proposal
Revises: 008_commercial
Create Date: 2026-08-15 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "009_proposal"
down_revision: str | None = "008_commercial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1490-1550 - transcribed verbatim.
    op.create_table(
        "proposal_sections",
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
        sa.Column("section_name", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'draft'")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('draft','review','approved')",
            name="ck_proposal_section_status",
        ),
        sa.UniqueConstraint(
            "tender_id", "section_name", "version", name="uq_proposal_section_version"
        ),
    )

    op.create_table(
        "proposal_claims",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "section_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposal_sections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.Text(), nullable=False),
        sa.Column("is_factual", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "grounding_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'needs_review'"),
        ),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "claim_type IN ("
            "'capability','experience','team','compliance','commercial','other'"
            ")",
            name="ck_proposal_claim_type",
        ),
        sa.CheckConstraint(
            "grounding_status IN ('grounded','unsupported','needs_review','not_required')",
            name="ck_proposal_claim_grounding_status",
        ),
        sa.UniqueConstraint(
            "section_id", "content_fingerprint", name="uq_proposal_claim_fingerprint"
        ),
    )

    op.create_table(
        "proposal_claim_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "proposal_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposal_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "proposal_claim_id", "evidence_link_id", name="uq_proposal_claim_evidence"
        ),
    )

    op.create_table(
        "proposal_claim_requirements",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "proposal_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposal_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "proposal_claim_id", "requirement_id", name="uq_proposal_claim_requirement"
        ),
    )

    op.create_table(
        "proposal_claim_employees",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "proposal_claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("proposal_claims.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "proposal_claim_id",
            "employee_knowledge_item_id",
            name="uq_proposal_claim_employee",
        ),
    )


def downgrade() -> None:
    op.drop_table("proposal_claim_employees")
    op.drop_table("proposal_claim_requirements")
    op.drop_table("proposal_claim_evidence")
    op.drop_table("proposal_claims")
    op.drop_table("proposal_sections")
