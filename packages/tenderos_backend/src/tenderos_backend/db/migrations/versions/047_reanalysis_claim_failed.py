"""reanalysis_claims: a claim can end failed, with its code

Plan item 124 (user decisions 2026-11-27, Q1 to Q8 a).

A reanalysis claim had two statuses, running and completed, and only the
job's last statement completed it. A job that raised, timed out or lost its
worker left its claim running for good: the lease let a second delivery take
it over, but nothing delivers a dispatched event again. Now the job ends its
own claim failed when it cannot finish (GRAPH_STALE after its retries, or the
exception's class name), and the dispatcher's reaper ends the claim of a job
RQ says will never finish, or whose lease lapsed (WORKER_LOST, with the
rule). A failed claim is terminal: the take-over matches running only.

Columns:
- failed_at and error_code, both set exactly when status is failed;
- reaped_rule, set only on a failed claim the reaper ended (the RQ verdict,
  or "lease"); null when the job ended its own claim.

A partial index on created_at for running claims, the reaper's scan.

The view observability.reanalysis_claims, for the metrics exporter: status,
error_code, reaped_rule and whether a running claim's lease lapsed. No ids,
no tender, no item. tenderos_backend.db.observability builds the same view
on the test database.

No backfill: the claims running today are failed by the reaper's first pass,
the same path as any other.

Downgrade puts failed claims back to running (what they were before this
migration; their lease is left as it was), then drops the view, the index,
the checks and the columns, and restores the two-status check. The rows stay:
agent traces point at them.

Revision ID: 047_reanalysis_claim_failed
Revises: 046_requirement_keys
Create Date: 2026-11-27 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "047_reanalysis_claim_failed"  # alembic_version holds 32 characters
down_revision: str | None = "046_requirement_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW = (
    "CREATE VIEW observability.reanalysis_claims AS "
    "SELECT status, error_code, reaped_rule, "
    "(status = 'running' AND locked_until < now()) AS lease_lapsed "
    "FROM public.reanalysis_claims"
)


def upgrade() -> None:
    op.add_column(
        "reanalysis_claims", sa.Column("failed_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("reanalysis_claims", sa.Column("error_code", sa.Text(), nullable=True))
    op.add_column("reanalysis_claims", sa.Column("reaped_rule", sa.Text(), nullable=True))
    op.drop_constraint("ck_reanalysis_claim_status", "reanalysis_claims", type_="check")
    op.create_check_constraint(
        "ck_reanalysis_claim_status",
        "reanalysis_claims",
        "status IN ('running','completed','failed')",
    )
    op.create_check_constraint(
        "ck_reanalysis_claim_failed_at_pair",
        "reanalysis_claims",
        "(status = 'failed') = (failed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_reanalysis_claim_error_code_pair",
        "reanalysis_claims",
        "(status = 'failed') = (error_code IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_reanalysis_claim_reaped_rule_failed",
        "reanalysis_claims",
        "reaped_rule IS NULL OR status = 'failed'",
    )
    op.create_index(
        "idx_reanalysis_claims_running",
        "reanalysis_claims",
        ["created_at"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.execute(_VIEW)


def downgrade() -> None:
    op.execute("DROP VIEW observability.reanalysis_claims")
    op.execute(
        "UPDATE reanalysis_claims SET status = 'running', failed_at = NULL, error_code = NULL, "
        "reaped_rule = NULL WHERE status = 'failed'"
    )
    op.drop_index("idx_reanalysis_claims_running", table_name="reanalysis_claims")
    op.drop_constraint("ck_reanalysis_claim_reaped_rule_failed", "reanalysis_claims", type_="check")
    op.drop_constraint("ck_reanalysis_claim_error_code_pair", "reanalysis_claims", type_="check")
    op.drop_constraint("ck_reanalysis_claim_failed_at_pair", "reanalysis_claims", type_="check")
    op.drop_constraint("ck_reanalysis_claim_status", "reanalysis_claims", type_="check")
    op.create_check_constraint(
        "ck_reanalysis_claim_status",
        "reanalysis_claims",
        "status IN ('running','completed')",
    )
    op.drop_column("reanalysis_claims", "reaped_rule")
    op.drop_column("reanalysis_claims", "error_code")
    op.drop_column("reanalysis_claims", "failed_at")
