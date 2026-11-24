"""Migration 041 (plan item 90, PR 33): the six indexes, and that each path
they were added for can use them.

- On a scratch database: upgrade from 040 builds the six indexes valid,
  with the partial predicate; downgrade drops them; upgrading again rebuilds
  them. test_schema_diff.py separately checks that they match the models.
- On the same migrated database (the observability views exist only there,
  not in the test database built from the models): every LLM dashboard panel
  query can use idx_agent_traces_created_at, and the metrics exporter's
  runs-ended-by-the-system query can use idx_audit_run_endings, which needs
  the partial index's predicate to match the view's. A later change to
  either predicate fails here instead of silently going back to a scan.
- On the test database (built from the models): the knowledge reach
  lookup's union, the statement KnowledgeRepository._tender_ids_reaching
  sends, filters each of the four tables on the column its new index leads
  with. Structural, not a plan: on near-empty tables the planner may serve
  role_matches through its unique index (where the item is the second
  column) even for a lookup by item alone, as two full-suite runs showed, so
  a plan here would not be stable. Which index the planner picks with real
  statistics is measured on the seeded scratch databases (pr33-plan.md).

"Can use" is checked with sequential scans disabled for the EXPLAIN: these
tables are near empty, where the planner rightly prefers a scan. With scans
off, a usable index appears in the plan and an unusable one cannot.
"""

import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from tenderos_backend.core.config import settings
from tenderos_backend.db.session import engine as app_engine
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_worker import metrics_exporter

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "040_observability_grafana_views"
AFTER = "041_lookup_and_range_indexes"
# name -> (table, column)
LOOKUPS = {
    "idx_evidence_links_knowledge_item": ("evidence_links", "knowledge_item_id"),
    "idx_tender_knowledge_selections_item": ("tender_knowledge_selections", "knowledge_item_id"),
    "idx_role_matches_employee_item": ("role_matches", "employee_knowledge_item_id"),
    "idx_proposal_claim_employees_item": ("proposal_claim_employees", "employee_knowledge_item_id"),
}
LOOKUP_INDEXES = {
    name: f"{table} USING btree ({column})" for name, (table, column) in LOOKUPS.items()
}
NAMES = (*LOOKUP_INDEXES, "idx_agent_traces_created_at", "idx_audit_run_endings")


def _sync_dsn(async_dsn: str, database: str | None = None) -> str:
    dsn = async_dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database is not None:
        dsn = re.sub(r"/[^/?]+(\?.*)?$", f"/{database}", dsn)
    return dsn


def _execute_as_admin(admin_dsn: str, statement: str) -> None:
    engine = create_engine(admin_dsn, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(statement))
    finally:
        engine.dispose()


@pytest.fixture
def scratch(monkeypatch) -> Iterator[tuple[Config, str]]:
    name = f"migration_041_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        # env.py reads settings.postgres_dsn and builds an async engine.
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield cfg, _sync_dsn(admin_dsn, name)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _indexes(dsn: str) -> dict[str, tuple[bool, str]]:
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, i.indisvalid, pg_get_indexdef(i.indexrelid) "
                    "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = ANY(:names)"
                ),
                {"names": list(NAMES)},
            )
            return {name: (valid, definition) for name, valid, definition in rows}
    finally:
        engine.dispose()


def _plan_without_seq_scans(dsn: str, sql: str) -> str:
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            rows = conn.exec_driver_sql("EXPLAIN " + sql)
            return "\n".join(row[0] for row in rows)
    finally:
        engine.dispose()


def _dashboard_queries() -> list[tuple[str, str]]:
    """Every LLM dashboard panel's SQL, Grafana's macros expanded as its
    Postgres datasource expands them, over the dashboard's default 6 h."""
    dashboard = json.loads((REPO_ROOT / "observability/dashboards/llm.json").read_text())
    queries = []
    for panel in dashboard["panels"]:
        for target in panel.get("targets", []):
            if "rawSql" in target:
                sql = (
                    target["rawSql"]
                    .replace(
                        "$__timeFilter(created_at)",
                        "created_at BETWEEN now() - interval '6 hours' AND now()",
                    )
                    .replace(
                        "$__timeGroupAlias(created_at, '1h')",
                        'floor(extract(epoch from created_at)/3600)*3600 AS "time"',
                    )
                )
                queries.append((panel["title"], sql))
    return queries


def test_upgrade_builds_the_six_indexes_valid_and_downgrade_drops_them(scratch):
    cfg, dsn = scratch
    assert _indexes(dsn) == {}

    command.upgrade(cfg, AFTER)
    built = _indexes(dsn)
    assert sorted(built) == sorted(NAMES)
    assert all(valid for valid, _ in built.values()), built
    for name, target in LOOKUP_INDEXES.items():
        assert built[name][1].endswith(f"ON public.{target}"), built[name]
    assert built["idx_agent_traces_created_at"][1].endswith(
        "ON public.agent_traces USING btree (created_at)"
    )
    assert built["idx_audit_run_endings"][1].endswith(
        "ON public.audit_events USING btree (action) WHERE (action = ANY "
        "(ARRAY['workflow_run_reaped'::text, 'workflow_run_timed_out'::text]))"
    )

    command.downgrade(cfg, BEFORE)
    assert _indexes(dsn) == {}

    command.upgrade(cfg, AFTER)
    assert sorted(_indexes(dsn)) == sorted(NAMES)


def test_the_dashboard_and_the_exporter_can_use_their_indexes(scratch):
    cfg, dsn = scratch
    command.upgrade(cfg, AFTER)

    queries = _dashboard_queries()
    assert len(queries) == 8
    for title, sql in queries:
        assert "idx_agent_traces_created_at" in _plan_without_seq_scans(dsn, sql), title

    assert "idx_audit_run_endings" in _plan_without_seq_scans(dsn, metrics_exporter._REAPED)


@pytest.mark.asyncio
async def test_the_knowledge_reach_lookup_filters_on_the_four_indexed_columns():
    captured: list[tuple[str, tuple]] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        captured.append((statement, tuple(parameters or ())))

    event.listen(app_engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with UnitOfWork() as uow:
            await uow.knowledge._tender_ids_reaching({uuid.uuid4()})
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", capture)
    assert len(captured) == 1
    statement = captured[0][0]

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            text(
                "SELECT c.relname, a.attname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = i.indkey[0] "
                "WHERE c.relname = ANY(:names)"
            ),
            {"names": list(LOOKUPS)},
        )
        leading = dict(result.tuples().all())
    for name, (table, column) in LOOKUPS.items():
        # The app's statement looks the table up by that column...
        assert f"{table}.{column} IN" in statement, (table, column, statement)
        # ...and the test database (built from the models) has the index
        # leading with it.
        assert leading.get(name) == column, (name, leading)
