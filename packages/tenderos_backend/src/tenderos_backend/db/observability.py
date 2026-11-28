"""Observability PRs 31 and 32: the `observability` schema's names, its
readers, and its views for the models' build.

The schema holds views only, each exposing only the columns and rows a
reader needs (decision Q6 a, and the readers' access decided 2026-11-22).
Migrations 039 and 040 create the views with their own literal SQL, so the
migrations stay frozen; VIEWS here builds the same views on the test
database, which is made from the models rather than the migrations
(tests/models_schema.py). tests/integration/test_schema_diff.py compares the
views of both builds, so the two copies cannot drift.

Each reader is its own login role with its own views (READERS), granted by
name, never "everything in the schema" (PR 32, answer B): the exporter
cannot read Grafana's wider views, and Grafana does not get the exporter's.
tests/unit/test_observability_readers.py fails when a view belongs to no
reader.

Imports nothing from the settings, so the metrics_exporter (which has none
of the app's environment) can import the names.
"""

from dataclasses import dataclass

SCHEMA = "observability"


@dataclass(frozen=True)
class Reader:
    """One login role that reads the schema. Created and granted by
    tenderos_backend.db.observability_role in the migrate service, never by
    a migration: a role is server-wide and needs a password."""

    role: str
    # The variable in .env holding its password (the value is never logged).
    # Empty: the setup step skips this reader.
    login_variable: str
    views: tuple[str, ...]
    statement_timeout: str


EXPORTER_ROLE = "tenderos_metrics_exporter"
EXPORTER_LOGIN_VARIABLE = "METRICS_EXPORTER_DB_PASSWORD"
EXPORTER_VIEWS = (
    "workflow_runs",
    "operations",
    "outbox_events",
    "runs_ended_by_system",
    "reanalysis_claims",
)

GRAFANA_ROLE = "tenderos_grafana_reader"
GRAFANA_LOGIN_VARIABLE = "GRAFANA_DB_PASSWORD"
GRAFANA_VIEWS = (
    "llm_calls",
    "run_history",
    "operation_history",
    "dead_letters",
    "run_endings",
    "readiness_reports",
    "graph_projection_runs",
    "gate_waits",
)

EXPORTER = Reader(EXPORTER_ROLE, EXPORTER_LOGIN_VARIABLE, EXPORTER_VIEWS, "5s")
# 10 s: the LLM dashboard's range queries (percentiles over a week) read more
# rows than the exporter's counts.
GRAFANA = Reader(GRAFANA_ROLE, GRAFANA_LOGIN_VARIABLE, GRAFANA_VIEWS, "10s")
READERS = (EXPORTER, GRAFANA)

# (name, SELECT) for each view: the exporter's four as migration 039 creates
# them and its fifth as migration 047 does, then Grafana's eight as migration
# 040 does (readiness_reports as 044 replaced it). Ids only, never a tender's
# or an organization's name, a payload, free text or before/after values.
VIEWS: tuple[tuple[str, str], ...] = (
    ("workflow_runs", "SELECT status, created_at, current_job_at FROM public.workflow_runs"),
    ("operations", "SELECT status, created_at, started_at FROM public.async_operations"),
    ("outbox_events", "SELECT status, event_type FROM public.outbox_events"),
    (
        "runs_ended_by_system",
        "SELECT action, metadata ->> 'rule' AS rule FROM public.audit_events "
        "WHERE action IN ('workflow_run_reaped', 'workflow_run_timed_out')",
    ),
    (
        # Plan item 124, migration 047: the exporter's claim counts. No ids,
        # no tender, no item.
        "reanalysis_claims",
        "SELECT status, error_code, reaped_rule, "
        "(status = 'running' AND locked_until < now()) AS lease_lapsed "
        "FROM public.reanalysis_claims",
    ),
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
        # Plan item 120 (migration 044): generation appended, so the Work
        # dashboard picks each tender's newest report by it, not by a clock.
        "SELECT tender_id, tender_state_version, ready, generated_at, last_generated_at, "
        'blockers @> \'[{"category": "stale_graph_projection"}]\'::jsonb AS stale_graph, '
        "generation FROM public.readiness_reports",
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


def create_views_statements() -> list[str]:
    """The statements that build the schema and its views on a database
    built from the models. CREATE OR REPLACE, so a schema left behind by an
    earlier build is reused."""
    statements = [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"]
    statements += [f"CREATE OR REPLACE VIEW {SCHEMA}.{name} AS {select}" for name, select in VIEWS]
    return statements
