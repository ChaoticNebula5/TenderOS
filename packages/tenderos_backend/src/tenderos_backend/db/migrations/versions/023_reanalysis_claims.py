"""reanalysis_claims: refuse a duplicate delivery of one selection change

Phase 4 plan item 18 (decision 0036). reanalyze_knowledge_selection_change
was the one outbox consumer with no duplicate guard: a second delivery of
the same KnowledgeSelectionChanged repeated the compliance re-evaluation
(paid LLM calls), compliance's unconditional state_version bump and the
graph-contradiction findings. RQ's unique=True covers a duplicate only
while the first job's record is still in Redis.

The job inserts one row per (tender_id, knowledge_item_id, state_version)
before any work, with ON CONFLICT DO NOTHING: no row back means the change
was already claimed. A running claim whose lease (locked_until) has lapsed
is taken over in one conditional UPDATE; the holder marks it completed at
the end, only while its claim_token is still current.

knowledge_item_id has no foreign key: the row records that a delivery ran,
and must neither block deleting the item nor disappear with it.

Revision ID: 023_reanalysis_claims
Revises: 022_one_active_run_per_tender
Create Date: 2026-11-04 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023_reanalysis_claims"
down_revision: str | None = "022_one_active_run_per_tender"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reanalysis_claims",
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
        sa.Column("knowledge_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tender_id", "knowledge_item_id", "state_version", name="uq_reanalysis_claim"
        ),
        sa.CheckConstraint(
            "status IN ('running','completed')", name="ck_reanalysis_claim_status"
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="ck_reanalysis_claim_completed_pair",
        ),
    )


def downgrade() -> None:
    op.drop_table("reanalysis_claims")
