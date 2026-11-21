"""agent_traces.attempts: the provider requests one LLM call made

PR 28 (observability phase, LLM measurements; user decision 2026-11-21,
Q9 a). latency_ms already held one logical call's time, but nothing said
how many provider requests that time covered: the Gemini adapter's
rate-limit and 5xx retries and I4's repair call left no trace. This column
sits beside it:
- 0 for a cache hit (no request);
- 1 for a call with no retry;
- more for each retry, and for the repair call.

ck_agent_trace_attempts refuses a negative count.

Upgrade adds the column, nullable with no default and no backfill: the rows
written before it do not know their attempts, and the report shows NULL as
unknown rather than a guessed 1. Then the constraint. Downgrade drops the
constraint, then the column.

Revision ID: 036_agent_trace_attempts
Revises: 035_requirement_check_time
Create Date: 2026-11-21 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "036_agent_trace_attempts"
down_revision: str | None = "035_requirement_check_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_traces", sa.Column("attempts", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_agent_trace_attempts", "agent_traces", "attempts IS NULL OR attempts >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_trace_attempts", "agent_traces", type_="check")
    op.drop_column("agent_traces", "attempts")
