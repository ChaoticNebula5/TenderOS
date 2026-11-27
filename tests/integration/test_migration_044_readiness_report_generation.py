"""Migration 044 on a real database at 043 (plan item 120): readiness_reports
gains generation, from readiness_report_generation_seq, numbered for the
existing rows in the order "latest" used before (last_generated_at, then
id), so each tender's current report is unchanged; the sequence continues
past them; (tender_id, generation) is unique; the observability view gains
the column.

Built like test_migration_043_compliance_automated_answer.py: a scratch
database upgraded to 043 with real Alembic, seeded with raw SQL with foreign
keys off (session_replication_role=replica), so no tender rows are needed.
Helpers are copied, this codebase's convention for tests.

Slower than a typical test: each one runs the whole migration history.
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "043_compliance_automated_answer"
AFTER = "044_readiness_report_generation"


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


@dataclass
class Scratch:
    dsn: str
    cfg: Config

    def version(self) -> str:
        return self.rows("SELECT version_num FROM alembic_version")[0]["version_num"]

    def rows(self, sql: str, params: dict | None = None) -> list[dict]:
        engine = create_engine(self.dsn)
        try:
            with engine.connect() as conn:
                return [dict(r) for r in conn.execute(text(sql), params or {}).mappings()]
        finally:
            engine.dispose()

    def execute(self, sql: str, params: dict | None = None) -> None:
        engine = create_engine(self.dsn)
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL session_replication_role = replica"))
                conn.execute(text(sql), params or {})
        finally:
            engine.dispose()


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_044_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield Scratch(dsn=_sync_dsn(admin_dsn, name), cfg=cfg)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _report(
    db: Scratch, tender: uuid.UUID, at: str, report_id: uuid.UUID | None = None
) -> uuid.UUID:
    report_id = report_id or uuid.uuid4()
    db.execute(
        "INSERT INTO readiness_reports "
        "(id, tender_id, tender_state_version, counts, blockers, ready, content_hash, "
        "last_generated_at) VALUES (:id, :tender, 1, '{}'::jsonb, '[]'::jsonb, true, :hash, :at)",
        {"id": report_id, "tender": tender, "hash": uuid.uuid4().hex, "at": at},
    )
    return report_id


def _generations(db: Scratch) -> dict[uuid.UUID, int]:
    return {
        row["id"]: row["generation"]
        for row in db.rows("SELECT id, generation FROM readiness_reports")
    }


def _latest_by_the_clock(db: Scratch, tender: uuid.UUID) -> uuid.UUID:
    return db.rows(
        "SELECT id FROM readiness_reports WHERE tender_id = :t "
        "ORDER BY last_generated_at DESC, id DESC LIMIT 1",
        {"t": tender},
    )[0]["id"]


def _latest_by_generation(db: Scratch, tender: uuid.UUID) -> uuid.UUID:
    return db.rows(
        "SELECT id FROM readiness_reports WHERE tender_id = :t ORDER BY generation DESC LIMIT 1",
        {"t": tender},
    )[0]["id"]


def test_upgrade_numbers_existing_reports_in_todays_order_and_continues_past_them(
    scratch: Scratch,
):
    first_tender, second_tender = uuid.uuid4(), uuid.uuid4()
    # A tie on the stamp is broken by id, as the old order did.
    low_id, high_id = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    early = _report(scratch, first_tender, "2026-11-25 10:00:00+00")
    tie_low = _report(scratch, first_tender, "2026-11-25 11:00:00+00", low_id)
    tie_high = _report(scratch, first_tender, "2026-11-25 11:00:00+00", high_id)
    other = _report(scratch, second_tender, "2026-11-25 10:30:00+00")
    latest_before = {t: _latest_by_the_clock(scratch, t) for t in (first_tender, second_tender)}

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert _generations(scratch) == {early: 1, other: 2, tie_low: 3, tie_high: 4}
    assert {t: _latest_by_generation(scratch, t) for t in latest_before} == latest_before
    inserted = uuid.uuid4()
    scratch.execute(
        "INSERT INTO readiness_reports "
        "(id, tender_id, tender_state_version, counts, blockers, ready, content_hash) "
        "VALUES (:id, :tender, 1, '{}'::jsonb, '[]'::jsonb, true, 'next')",
        {"id": inserted, "tender": second_tender},
    )
    assert _generations(scratch)[inserted] == 5, "the sequence continues past the backfill"
    with pytest.raises(IntegrityError, match="uq_readiness_report_generation"):
        scratch.execute(
            "INSERT INTO readiness_reports "
            "(id, tender_id, tender_state_version, counts, blockers, ready, content_hash, "
            "generation) VALUES (:id, :tender, 1, '{}'::jsonb, '[]'::jsonb, true, 'dup', 5)",
            {"id": uuid.uuid4(), "tender": second_tender},
        )
    view_columns = [
        row["column_name"]
        for row in scratch.rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'observability' AND table_name = 'readiness_reports' "
            "ORDER BY ordinal_position"
        )
    ]
    assert view_columns[-1] == "generation"


def test_upgrade_on_an_empty_table_starts_the_sequence_at_one(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)
    inserted = uuid.uuid4()
    scratch.execute(
        "INSERT INTO readiness_reports "
        "(id, tender_id, tender_state_version, counts, blockers, ready, content_hash) "
        "VALUES (:id, :tender, 1, '{}'::jsonb, '[]'::jsonb, true, 'first')",
        {"id": inserted, "tender": uuid.uuid4()},
    )
    assert _generations(scratch) == {inserted: 1}


def test_downgrade_removes_the_order_and_upgrade_runs_again(scratch: Scratch):
    tender = uuid.uuid4()
    report = _report(scratch, tender, "2026-11-25 10:00:00+00")
    command.upgrade(scratch.cfg, AFTER)

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    columns = {
        (row["table_schema"], row["column_name"])
        for row in scratch.rows(
            "SELECT table_schema, column_name FROM information_schema.columns "
            "WHERE table_name = 'readiness_reports'"
        )
    }
    assert ("public", "generation") not in columns
    assert ("observability", "generation") not in columns
    assert ("observability", "stale_graph") in columns, "the view is back as it was"
    assert scratch.rows(
        "SELECT count(*) AS n FROM pg_class WHERE relname = 'readiness_report_generation_seq'"
    ) == [{"n": 0}]
    assert [row["id"] for row in scratch.rows("SELECT id FROM readiness_reports")] == [report]

    command.upgrade(scratch.cfg, AFTER)

    assert _generations(scratch) == {report: 1}
