"""agent_traces: rate_limited_requests and rate_limit_wait_ms

PR 34 (the real Gemini run's prerequisites; user decisions 2026-11-25).

A call that waited on 429s and then succeeded showed only attempts above 1,
the same as one that waited on a 5xx, and its latency_ms included the waits
with nothing saying how long they were. Two nullable columns record them:
- rate_limited_requests: how many of the call's requests the provider
  answered 429 (they are among those counted in attempts);
- rate_limit_wait_ms: the time the adapter spent waiting on them, inside
  latency_ms, so latency without the waits is latency_ms minus it.
A 5xx backoff is not counted in either.

Three checks: neither is negative, and rate_limited_requests is at most
attempts when both are known.

Upgrade adds the columns, nullable with no default and no backfill (older
rows did not record their 429s; they read unknown), then the checks.
Downgrade drops them in reverse.

Revision ID: 042_agent_trace_rate_limits
Revises: 041_lookup_and_range_indexes
Create Date: 2026-11-25 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "042_agent_trace_rate_limits"  # alembic_version holds 32 characters
down_revision: str | None = "041_lookup_and_range_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_traces", sa.Column("rate_limited_requests", sa.Integer(), nullable=True))
    op.add_column("agent_traces", sa.Column("rate_limit_wait_ms", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_agent_trace_rate_limited_requests",
        "agent_traces",
        "rate_limited_requests IS NULL OR rate_limited_requests >= 0",
    )
    op.create_check_constraint(
        "ck_agent_trace_rate_limit_wait_ms",
        "agent_traces",
        "rate_limit_wait_ms IS NULL OR rate_limit_wait_ms >= 0",
    )
    op.create_check_constraint(
        "ck_agent_trace_rate_limited_within_attempts",
        "agent_traces",
        "rate_limited_requests IS NULL OR attempts IS NULL OR rate_limited_requests <= attempts",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_trace_rate_limited_within_attempts", "agent_traces", type_="check")
    op.drop_constraint("ck_agent_trace_rate_limit_wait_ms", "agent_traces", type_="check")
    op.drop_constraint("ck_agent_trace_rate_limited_requests", "agent_traces", type_="check")
    op.drop_column("agent_traces", "rate_limit_wait_ms")
    op.drop_column("agent_traces", "rate_limited_requests")
