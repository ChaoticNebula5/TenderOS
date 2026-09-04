"""workflow_runs.retry_count: the real countdown for B16 Group 4's retry policy

Revision ID: 016_workflow_run_retry_count
Revises: 015_pricing_finding_document
Create Date: 2026-09-02 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "016_workflow_run_retry_count"
down_revision: str | None = "015_pricing_finding_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # TenderState.retry_budget_remaining (the LangGraph-checkpointed state
    # field major finding #17 originally named) turned out to be the wrong
    # place to track this from OUTSIDE the graph - it lives inside
    # LangGraph's own checkpointed state, not a plain column, so nothing
    # outside a running graph.ainvoke() call can read or decrement it
    # without re-deriving LangGraph's own checkpoint-serialization format.
    # This column is the real, mutable countdown the retry-scheduling code
    # in jobs/workflow.py actually uses; retry_budget_remaining's seed
    # value (10) is reused as this column's own ceiling, read as a
    # constant, not as the live counter. See KNOWN_LIMITATIONS.md's major
    # finding #17 for the full reasoning behind this deviation.
    op.add_column(
        "workflow_runs",
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("workflow_runs", "retry_count")
