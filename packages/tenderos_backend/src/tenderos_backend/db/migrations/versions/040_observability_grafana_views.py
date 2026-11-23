"""observability schema: the views Grafana's dashboards read

PR 32 (observability phase; decision Q6 a with views, and the plan's answers
of 2026-11-22: C, all four differences from the design's table). Grafana's
Postgres datasource connects as its own login role, which reads these views
and nothing else: not `public`, and not migration 039's exporter views (each
reader is granted its own views by name, db/observability.py's READERS).
Postgres runs a view with its owner's rights unless it is security_invoker
(off here, the default), so the role needs no grant on the tables
underneath.

No role here, as in 039: the migrate service's setup step
(tenderos_backend.db.observability_role) creates the login from
GRAFANA_DB_PASSWORD in .env and grants it these views on every start.

Named apart from 039's four (workflow_runs, operations, outbox_events,
runs_ended_by_system), which stay as they are. Ids only, never a tender's or
an organization's name, a payload, free text (error_message) or audit
before/after values:

- llm_calls: agent_traces, with the tender's organization when the row has
  none of its own; the three owners (run, operation, reanalysis claim), the
  model, status, error_code, attempts, latency, tokens (thinking included)
  and estimated cost; no prompt, refs or tool names
- run_history: workflow_runs with the tender's organization; status,
  error_code, retry_count and the three timestamps
- operation_history: async_operations; kind, status, error_code and times
- dead_letters: outbox_events rows that failed every attempt, filtered in
  the view; type, aggregate, attempts, times and the request id (to find
  their log lines); no payload or idempotency key
- run_endings: the workflow_run_reaped and workflow_run_timed_out audit
  rows only, filtered in the view, with the reaper's rule
- readiness_reports: the report's version, ready and times, and a
  stale_graph flag (a blocker of category stale_graph_projection), not the
  blockers themselves
- graph_projection_runs: version, status, counts and times; no error_message
- gate_waits: pending approval gates only, with requested_at, which is when
  the run's wait at the gate began (the run itself records no such time)

The test database is built from the models, not migrations
(tests/models_schema.py), so the same views are created there from
tenderos_backend.db.observability, and tests/integration/test_schema_diff.py
compares the two builds' views.

Revision ID: 040_observability_grafana_views
Revises: 039_observability_exporter_views
Create Date: 2026-11-22 00:00:00

"""

from collections.abc import Sequence

from alembic import op

revision: str = "040_observability_grafana_views"  # alembic_version holds 32 characters
down_revision: str | None = "039_observability_exporter_views"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEWS = (
    (
        "llm_calls",
        "SELECT t.id, t.created_at, "
        "coalesce(t.organization_id, tn.organization_id) AS organization_id, t.tender_id, "
        "t.workflow_run_id, t.operation_id, t.reanalysis_claim_id, t.agent_name, "
        "t.provider, t.model, t.status, t.error_code, t.attempts, t.latency_ms, t.input_tokens, "
        "t.output_tokens, t.thinking_tokens, t.estimated_cost_usd "
        "FROM public.agent_traces t LEFT JOIN public.tenders tn ON tn.id = t.tender_id",
    ),
    (
        "run_history",
        "SELECT r.id, r.tender_id, tn.organization_id, r.status, r.error_code, r.retry_count, "
        "r.created_at, r.started_at, r.completed_at "
        "FROM public.workflow_runs r JOIN public.tenders tn ON tn.id = r.tender_id",
    ),
    (
        "operation_history",
        "SELECT id, organization_id, tender_id, kind, status, error_code, created_at, started_at, "
        "finished_at FROM public.async_operations",
    ),
    (
        "dead_letters",
        "SELECT id, aggregate_type, aggregate_id, event_type, attempt_count, created_at, "
        "available_at, request_id FROM public.outbox_events WHERE status = 'failed'",
    ),
    (
        "run_endings",
        "SELECT id, organization_id, tender_id, workflow_run_id, action, "
        "metadata ->> 'rule' AS rule, created_at FROM public.audit_events "
        "WHERE action IN ('workflow_run_reaped', 'workflow_run_timed_out')",
    ),
    (
        "readiness_reports",
        "SELECT tender_id, tender_state_version, ready, generated_at, last_generated_at, "
        'blockers @> \'[{"category": "stale_graph_projection"}]\'::jsonb AS stale_graph '
        "FROM public.readiness_reports",
    ),
    (
        "graph_projection_runs",
        "SELECT tender_id, source_state_version, status, node_count, relationship_count, "
        "started_at, completed_at FROM public.graph_projection_runs",
    ),
    (
        "gate_waits",
        "SELECT id, tender_id, workflow_run_id, gate_type, requested_at "
        "FROM public.approval_gates WHERE status = 'pending'",
    ),
)


def upgrade() -> None:
    for name, select in _VIEWS:
        op.execute(f"CREATE VIEW observability.{name} AS {select}")


def downgrade() -> None:
    for name, _ in reversed(_VIEWS):
        op.execute(f"DROP VIEW observability.{name}")
