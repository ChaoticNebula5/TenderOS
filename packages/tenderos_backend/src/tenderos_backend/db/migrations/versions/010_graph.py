"""graph: graph_projection_runs

Revision ID: 010_graph
Revises: 009_proposal
Create Date: 2026-08-16 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010_graph"
down_revision: str | None = "009_proposal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1711-1724 - transcribed verbatim. This table lives in
    # Postgres, not Neo4j - it's the audit/freshness-tracking record of each
    # projection run, read by the graph freshness invariant
    # (source_state_version vs tenders.state_version), not the graph data
    # itself.
    op.create_table(
        "graph_projection_runs",
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
        sa.Column("source_state_version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("node_count", sa.Integer(), nullable=True),
        sa.Column("relationship_count", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')",
            name="ck_graph_projection_run_status",
        ),
    )
    op.create_index(
        "idx_graph_projection_latest",
        "graph_projection_runs",
        ["tender_id", sa.text("source_state_version DESC")],
    )


def downgrade() -> None:
    op.drop_index("idx_graph_projection_latest", table_name="graph_projection_runs")
    op.drop_table("graph_projection_runs")
