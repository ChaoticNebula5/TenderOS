"""workflow_runs: a terminal 'changes_requested' status, and the runs it frees

Phase 4 plan item 71 (decision 0093). A reviewer's "Request changes" ends the
run at its gate: every gate node routes that decision to END, and nothing
resumes the thread. The run's coarse status used to be 'waiting_for_human',
an active status, so the tender's one active run
(uq_workflow_runs_one_active_per_tender, migration 022) was held for good:
no Start processing, no cancel in the web app, and the reaper leaves it alone
on purpose. 'changes_requested' is terminal. It is not one of Part C's six
values; it is added the way migration 018 added the cost_budget gate type.

The partial unique index (022) and the current-job index (024) list only the
active statuses, which do not change, so neither is touched.

Upgrade, in one transaction:
1. ck_workflow_run_status allows 'changes_requested'.
2. Recovery: a run left by the old behaviour moves to the new status. It is
   matched exactly: status 'waiting_for_human', its LATEST gate (newest
   requested_at, then id, the reaper's own ordering) decided
   'changes_requested', and no gate of the run still pending. A run waiting
   at a pending gate is legitimately waiting and is not touched.
   completed_at becomes that gate's decided_at, when the run really ended.

Downgrade maps every 'changes_requested' run to 'failed', then restores the
old CHECK. Not back to 'waiting_for_human': once a tender has started a new
run, a second active run for it violates the one-active index and the
downgrade would fail part way, and the old status strands the bid again
anyway. The outcome is not lost: the run's gate still records
'changes_requested' and the reviewer's notes.

Revision ID: 029_run_status_changes_requested
Revises: 028_refresh_token_resent_at
Create Date: 2026-11-13 00:00:00

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "029_run_status_changes_requested"
down_revision: str | None = "028_refresh_token_resent_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

# Literal lists, not imported from the models: a migration states the schema
# as it was at its own revision.
_OLD_STATUSES = ("queued", "running", "waiting_for_human", "completed", "failed", "cancelled")
_NEW_STATUSES = (*_OLD_STATUSES, "changes_requested")

RECOVER_STUCK_RUNS = sa.text(
    """
    UPDATE workflow_runs AS r
    SET status = 'changes_requested',
        completed_at = COALESCE(latest.decided_at, now())
    FROM (
        SELECT DISTINCT ON (workflow_run_id) workflow_run_id, status, decided_at
        FROM approval_gates
        ORDER BY workflow_run_id, requested_at DESC, id DESC
    ) AS latest
    WHERE latest.workflow_run_id = r.id
      AND r.status = 'waiting_for_human'
      AND latest.status = 'changes_requested'
      AND NOT EXISTS (
          SELECT 1 FROM approval_gates AS g
          WHERE g.workflow_run_id = r.id AND g.status = 'pending'
      )
    """
)

DOWNGRADE_TO_FAILED = sa.text(
    "UPDATE workflow_runs SET status = 'failed' WHERE status = 'changes_requested'"
)


def _status_check(statuses: Sequence[str]) -> None:
    op.drop_constraint("ck_workflow_run_status", "workflow_runs", type_="check")
    op.create_check_constraint(
        "ck_workflow_run_status",
        "workflow_runs",
        "status IN (" + ",".join(f"'{s}'" for s in statuses) + ")",
    )


def upgrade() -> None:
    _status_check(_NEW_STATUSES)
    moved = op.get_bind().execute(RECOVER_STUCK_RUNS).rowcount
    log.info("moved %d run(s) stuck by a Request changes decision to changes_requested", moved)


def downgrade() -> None:
    moved = op.get_bind().execute(DOWNGRADE_TO_FAILED).rowcount
    log.info("mapped %d changes_requested run(s) to failed", moved)
    _status_check(_OLD_STATUSES)
