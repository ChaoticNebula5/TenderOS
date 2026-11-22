"""Observability PR 31: the `observability` schema's names, and its views for
the models' build.

The schema holds views only, each exposing only the columns a reader needs
(decision Q6 a, and the exporter's access decided 2026-11-22). Migration 039
creates the views with its own literal SQL, so the migration stays frozen;
VIEWS here builds the same views on the test database, which is made from
the models rather than the migrations (tests/models_schema.py).
tests/integration/test_schema_diff.py compares the views of both builds, so
the two copies cannot drift.

Imports nothing from the settings, so the metrics_exporter (which has none
of the app's environment) can import the names.
"""

SCHEMA = "observability"

# The metrics_exporter's login role. Created and granted by
# tenderos_backend.db.observability_role in the migrate service, never by a
# migration: a role is server-wide and needs a password.
EXPORTER_ROLE = "tenderos_metrics_exporter"
# Its password, from .env (the value is never logged). Empty: the setup step
# is skipped and the exporter refuses to start.
EXPORTER_LOGIN_VARIABLE = "METRICS_EXPORTER_DB_PASSWORD"

# (name, SELECT) for each view, as migration 039 creates them.
VIEWS: tuple[tuple[str, str], ...] = (
    ("workflow_runs", "SELECT status, created_at, current_job_at FROM public.workflow_runs"),
    ("operations", "SELECT status, created_at, started_at FROM public.async_operations"),
    ("outbox_events", "SELECT status, event_type FROM public.outbox_events"),
    (
        "runs_ended_by_system",
        "SELECT action, metadata ->> 'rule' AS rule FROM public.audit_events "
        "WHERE action IN ('workflow_run_reaped', 'workflow_run_timed_out')",
    ),
)


def create_views_statements() -> list[str]:
    """The statements that build the schema and its views on a database
    built from the models. CREATE OR REPLACE, so a schema left behind by an
    earlier build is reused."""
    statements = [f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"]
    statements += [f"CREATE OR REPLACE VIEW {SCHEMA}.{name} AS {select}" for name, select in VIEWS]
    return statements
