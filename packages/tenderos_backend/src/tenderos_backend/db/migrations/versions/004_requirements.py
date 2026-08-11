"""requirements, submission_items, clarification_items

Revision ID: 004_requirements
Revises: 003_documents
Create Date: 2026-08-09 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_requirements"
down_revision: str | None = "003_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requirements",
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
        sa.Column("display_code", sa.Text(), nullable=False),
        sa.Column("requirement_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id"),
            nullable=False,
        ),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("human_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        # Additive beyond Part C's literal DDL - the table is created here for
        # the first time (no prior migration touched it), so this is a column
        # present at birth, not an ALTER on locked schema. Soft-delete: PATCH
        # .../reject sets this instead of removing the row, since requirements
        # is the parent of several CASCADE chains (clarification_items now;
        # compliance_entries/proposal_claim_requirements from B7/B9 on) that a
        # hard delete would silently take down with no per-row audit trail of
        # their own. Who/why lives in audit_events (actor already recorded
        # there) - no separate rejected_by column, that would duplicate it.
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
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
            "category IN ("
            "'technical','staffing','experience','commercial',"
            "'eligibility','submission','legal','security','other'"
            ")",
            name="ck_requirement_category",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_requirement_fingerprint"
        ),
    )
    op.create_index(
        "idx_requirements_tender_category",
        "requirements",
        ["tender_id", "category", "mandatory"],
    )

    op.create_table(
        "submission_items",
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
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id"),
            nullable=True,
        ),
        sa.Column(
            "matched_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="missing"),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('missing','present','needs_review','not_applicable')",
            name="ck_submission_item_status",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_submission_item_fingerprint"
        ),
    )

    op.create_table(
        "clarification_items",
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
            "requirement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("requirements.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('open','answered','waived','closed')",
            name="ck_clarification_item_status",
        ),
        sa.UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_clarification_item_fingerprint"
        ),
    )


    # E1: every LLM-backed agent "records model, tokens, latency, cost, and
    # tool names" - this table is where that goes. B5 is the first block with
    # a real (non-stub) agent, so it's the first one that needs this table to
    # exist, even though Part C lists it in the general schema block rather
    # than naming a specific build block as its owner.
    op.create_table(
        "agent_traces",
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
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent_name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("input_refs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("output_refs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("tool_names", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_agent_traces_tender_time", "agent_traces", ["tender_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_agent_traces_tender_time", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_table("clarification_items")
    op.drop_table("submission_items")
    op.drop_index("idx_requirements_tender_category", table_name="requirements")
    op.drop_table("requirements")
