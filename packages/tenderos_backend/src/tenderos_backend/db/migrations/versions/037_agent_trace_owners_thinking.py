"""agent_traces: operation_id, reanalysis_claim_id and thinking_tokens

PR 29 (observability phase; user decisions 2026-11-21, Q1 b and Q5 b).

Owners. A trace row could say only which workflow run it came from, so the
calls made by async operations (a red-team pass, a proposal generation, a
commercial validation) and by the reanalysis job were written with no owner
and fell outside cost per run and the cost-budget gate. Two nullable columns
name the other owners:
- operation_id -> async_operations.id;
- reanalysis_claim_id -> reanalysis_claims.id.
Both ON DELETE SET NULL, like workflow_run_id. ck_agent_trace_one_owner
allows at most one of workflow_run_id, operation_id and reanalysis_claim_id.
On-demand verification and knowledge imports own none.

Thinking. thinking_tokens holds the tokens a model spent thinking, apart
from output_tokens (the answer). Google bills them at the output rate, and
estimated_cost_usd includes them (gemini_adapter._estimate_cost_usd).
ck_agent_trace_thinking_tokens refuses a negative count.

Upgrade adds the three columns, nullable with no default and no backfill
(older rows do not know their owner or their thinking), their foreign keys,
then the two checks. No row violates the one-owner check, since none has an
operation or claim yet. Downgrade drops them in reverse.

Revision ID: 037_agent_trace_owners_thinking
Revises: 036_agent_trace_attempts
Create Date: 2026-11-21 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "037_agent_trace_owners_thinking"  # alembic_version holds 32 characters
down_revision: str | None = "036_agent_trace_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_traces", sa.Column("operation_id", UUID(as_uuid=True), nullable=True))
    op.add_column(
        "agent_traces", sa.Column("reanalysis_claim_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column("agent_traces", sa.Column("thinking_tokens", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "agent_traces_operation_id_fkey",
        "agent_traces",
        "async_operations",
        ["operation_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "agent_traces_reanalysis_claim_id_fkey",
        "agent_traces",
        "reanalysis_claims",
        ["reanalysis_claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_agent_trace_one_owner",
        "agent_traces",
        "num_nonnulls(workflow_run_id, operation_id, reanalysis_claim_id) <= 1",
    )
    op.create_check_constraint(
        "ck_agent_trace_thinking_tokens",
        "agent_traces",
        "thinking_tokens IS NULL OR thinking_tokens >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_trace_thinking_tokens", "agent_traces", type_="check")
    op.drop_constraint("ck_agent_trace_one_owner", "agent_traces", type_="check")
    op.drop_constraint("agent_traces_reanalysis_claim_id_fkey", "agent_traces", type_="foreignkey")
    op.drop_constraint("agent_traces_operation_id_fkey", "agent_traces", type_="foreignkey")
    op.drop_column("agent_traces", "thinking_tokens")
    op.drop_column("agent_traces", "reanalysis_claim_id")
    op.drop_column("agent_traces", "operation_id")
