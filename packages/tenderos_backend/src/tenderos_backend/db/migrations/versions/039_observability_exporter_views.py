"""observability schema: the views the metrics_exporter reads

PR 31 (observability phase; decisions Q3 a, Q4 c, Q6 a, and the exporter's
access decided 2026-11-22). The exporter connects as its own login role,
which can read this schema and nothing in `public`: these views expose only
the columns it uses. Postgres runs a view with its owner's rights unless it
is security_invoker (off here, the default), so the role needs no grant on
the tables underneath.

No role here: a role is server-wide and needs a password, so it is created
by a setup step in the `migrate` service after alembic
(tenderos_backend.db.observability_role), which also grants SELECT on
everything in this schema on every start, covering views later migrations
add. PR 32 adds Grafana's views to the same schema; a view gaining columns
there is CREATE OR REPLACE VIEW with the new columns after these.

- workflow_runs: status, created_at, current_job_at (runs by status; the
  oldest active one's age: a queued run's from created_at, a running run's
  from current_job_at, the run reaper's own measure)
- operations: status, created_at, started_at (async_operations, the same:
  a running operation's age from its claim)
- outbox_events: status, event_type (dead letters by event type)
- runs_ended_by_system: action and rule (`metadata->>'rule'`) of the
  workflow_run_reaped and workflow_run_timed_out rows only, filtered in the
  view, so the role cannot read any other audit event's action (reaped and
  timed-out runs by rule); no other metadata, no before or after

Nothing else: no payloads, no free-text error messages, no names.
The test database is built from the models, not migrations
(tests/models_schema.py), so the same views are created there from
tenderos_backend.db.observability, and tests/integration/test_schema_diff.py
compares the two builds' views.

Revision ID: 039_observability_exporter_views
Revises: 038_outbox_request_id
Create Date: 2026-11-22 00:00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "039_observability_exporter_views"  # alembic_version holds 32 characters
down_revision: str | None = "038_outbox_request_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS = (
    (
        "workflow_runs",
        "SELECT status, created_at, current_job_at FROM public.workflow_runs",
    ),
    (
        "operations",
        "SELECT status, created_at, started_at FROM public.async_operations",
    ),
    (
        "outbox_events",
        "SELECT status, event_type FROM public.outbox_events",
    ),
    (
        "runs_ended_by_system",
        "SELECT action, metadata ->> 'rule' AS rule FROM public.audit_events "
        "WHERE action IN ('workflow_run_reaped', 'workflow_run_timed_out')",
    ),
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA observability")
    for name, select in _VIEWS:
        op.execute(f"CREATE VIEW observability.{name} AS {select}")


def downgrade() -> None:
    for name, _ in reversed(_VIEWS):
        op.execute(f"DROP VIEW observability.{name}")
    op.execute("DROP SCHEMA observability")
