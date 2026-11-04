"""workflow_runs: the run's current RQ job, and why a failed run failed

Phase 4 plan item 19 (decision 0035, redesigned). Nothing recorded which RQ
job was moving a run forward, so nothing could tell a run whose job died
(worker killed, work horse OOM-killed, a retry lost from Redis) from one
waiting its turn: the run stayed active forever, and since item 43 its
tender could not start another.

- current_job_id: the RQ job now responsible for the run. Written by the
  claim that moves the run to running (the start and resume jobs) and by
  the scheduling of a retry, each in the same UPDATE as the move.
- current_job_at: when that was recorded, from the database clock. The
  reaper's 6-hour cap and its 60 s grace for a just-recorded retry are
  measured from it.
- error_code: why the run failed, for the screen: WORKER_LOST and
  DISPATCH_FAILED (the reaper) and JOB_TIMEOUT (RQ's job timeout). NULL
  for a run that failed any other way, as before. The CHECK keeps a typo
  from reaching the screen.

Backfill: runs already active get current_job_at = now() and no job id.
Nothing recorded their job, so only the cap can reap them, 6 hours after
this migration if they are still active. All three columns are nullable and
the downgrade drops them.

The codes are written out here rather than imported from the model, so
this migration stays what it was if the model's constant ever changes.

Revision ID: 024_workflow_run_current_job
Revises: 023_reanalysis_claims
Create Date: 2026-11-04 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024_workflow_run_current_job"
down_revision: str | None = "023_reanalysis_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("current_job_id", sa.Text(), nullable=True))
    op.add_column(
        "workflow_runs",
        sa.Column("current_job_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("workflow_runs", sa.Column("error_code", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_workflow_run_error_code",
        "workflow_runs",
        "error_code IN ('WORKER_LOST','DISPATCH_FAILED','JOB_TIMEOUT')",
    )
    op.execute(
        "UPDATE workflow_runs SET current_job_at = now() "
        "WHERE status IN ('queued', 'running', 'waiting_for_human')"
    )


def downgrade() -> None:
    op.drop_constraint("ck_workflow_run_error_code", "workflow_runs", type_="check")
    op.drop_column("workflow_runs", "error_code")
    op.drop_column("workflow_runs", "current_job_at")
    op.drop_column("workflow_runs", "current_job_id")
