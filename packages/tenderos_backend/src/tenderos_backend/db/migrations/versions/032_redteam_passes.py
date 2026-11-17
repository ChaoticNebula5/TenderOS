"""redteam_passes: one row per finished red-team pass

Phase 4 plan items 94 and 96 (user decision 2026-11-16, option A). Readiness
used to learn that the run's red-team pass failed only from degraded_stages,
LangGraph state its caller passed in: the Readiness page passed nothing, the
final gate passed the gate's frozen payload, so the two could disagree, and a
later successful manual pass could never clear the run's failure. Nothing
recorded which projection a pass had looked at either, so after a
re-projection with no new pass readiness trusted graph findings from an older
graph.

This table records each finished pass: who ran it (the run, or the manual
operation), whether it completed, whether its graph checks ran, the state
version read at the start of the pass (before any check), and a safe error
code for a failed pass.
redteam_service.run_red_team writes a completed row in the same transaction
as the pass's findings, and a failed row on its own. Readiness reads it.

No backfill (user decision, Q3): no history of passes exists, and a seeded
row would claim a pass that cannot be described. Existing bids show "No
red-team pass has been recorded" until their next pass.

Downgrade drops the table.

Revision ID: 032_redteam_passes
Revises: 031_document_chunk_origin
Create Date: 2026-11-17 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "032_redteam_passes"
down_revision: str | None = "031_document_chunk_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "redteam_passes",
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
            "operation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("async_operations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("graph_checks", sa.Text(), nullable=True),
        sa.Column("graph_state_version", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "finished_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("outcome IN ('completed','failed')", name="ck_redteam_pass_outcome"),
        sa.CheckConstraint(
            "graph_checks IS NULL OR graph_checks IN ('ran','skipped')",
            name="ck_redteam_pass_graph_checks",
        ),
        sa.CheckConstraint(
            "(outcome = 'completed' AND graph_checks IS NOT NULL AND error_code IS NULL)"
            " OR (outcome = 'failed' AND graph_checks IS NULL AND error_code IS NOT NULL)",
            name="ck_redteam_pass_shape",
        ),
        sa.CheckConstraint(
            "(graph_checks IS NOT DISTINCT FROM 'ran') = (graph_state_version IS NOT NULL)",
            name="ck_redteam_pass_graph_version",
        ),
    )
    op.create_index(
        "idx_redteam_pass_latest",
        "redteam_passes",
        ["tender_id", sa.text("finished_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_redteam_pass_latest", table_name="redteam_passes")
    op.drop_table("redteam_passes")
