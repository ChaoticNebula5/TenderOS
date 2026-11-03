"""workflow_runs: at most one active run per tender

Phase 4 plan item 43. Nothing stopped two runs of one tender from being
active at once: create_workflow_run inserted a run whenever every document
was parsed, and each click of Start processing sends a fresh
Idempotency-Key, so two tabs (or two clicks) each started one.

A partial unique index on tender_id over the active statuses (queued,
running, waiting_for_human) makes the second insert fail; the service maps
that to a 409 naming the active run. Terminal runs (completed, failed,
cancelled) are outside the index, so a tender can start again once its run
has finished.

The predicate is written out here rather than imported from the model, so
this migration stays what it was if the model's constant ever changes.

Fails on a database that already holds two active runs for one tender;
the dev database was checked before writing this (0 such tenders).

Revision ID: 022_one_active_run_per_tender
Revises: 021_refresh_tokens
Create Date: 2026-11-03 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "022_one_active_run_per_tender"
down_revision: str | None = "021_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_workflow_runs_one_active_per_tender",
        "workflow_runs",
        ["tender_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'waiting_for_human')"),
    )


def downgrade() -> None:
    op.drop_index("uq_workflow_runs_one_active_per_tender", table_name="workflow_runs")
