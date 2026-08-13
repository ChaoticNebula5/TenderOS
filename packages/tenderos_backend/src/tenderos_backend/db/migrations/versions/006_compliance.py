"""evidence and compliance: evidence_links, compliance_entries,
compliance_evidence

Revision ID: 006_compliance
Revises: 005_knowledge
Create Date: 2026-08-12 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006_compliance"
down_revision: str | None = "005_knowledge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1302-1358 - transcribed verbatim. knowledge_item_id and
    # source_chunk_id are both nullable with a CHECK requiring at least one:
    # most evidence will cite a B6 knowledge_item (employee/cert/project/
    # capability), but source_kind also allows 'document_excerpt' for
    # evidence cited directly from a tender document with no organizational
    # knowledge_item behind it at all (a later block's concern, not built by
    # B7, but the column shape has to allow it now since schema is locked).
    op.create_table(
        "evidence_links",
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
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "validity_status", sa.Text(), nullable=False, server_default=sa.text("'unknown'")
        ),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "source_kind IN ("
            "'employee','certification','past_project','capability',"
            "'company_document','client_reference','partner','document_excerpt'"
            ")",
            name="ck_evidence_link_source_kind",
        ),
        sa.CheckConstraint(
            "validity_status IN ('valid','invalid','expired','unknown')",
            name="ck_evidence_link_validity_status",
        ),
        sa.CheckConstraint(
            "knowledge_item_id IS NOT NULL OR source_chunk_id IS NOT NULL",
            name="ck_evidence_link_has_source",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_evidence_link_fingerprint"
        ),
    )

    op.create_table(
        "compliance_entries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reasoning_summary", sa.Text(), nullable=True),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column(
            "human_approved", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "last_evaluated_at",
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
            "status IN ("
            "'fully_compliant','partially_compliant','non_compliant',"
            "'clarification_required','evidence_unavailable','not_reviewed'"
            ")",
            name="ck_compliance_entry_status",
        ),
    )

    op.create_table(
        "compliance_evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "compliance_entry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("compliance_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "compliance_entry_id", "evidence_link_id", name="uq_compliance_evidence"
        ),
    )


def downgrade() -> None:
    op.drop_table("compliance_evidence")
    op.drop_table("compliance_entries")
    op.drop_table("evidence_links")
