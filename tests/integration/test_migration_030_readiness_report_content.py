"""Migration 030 on a real database at 029 (phase 4 plan item 93): readiness
reports become one row per distinct content at a version, with
content_hash and last_generated_at, and the unique key moves to (tender_id,
tender_state_version, content_hash).

Built like test_migration_029_run_status_changes_requested.py: a scratch
database upgraded to 029 with real Alembic, seeded with raw SQL with foreign
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
BEFORE = "029_run_status_changes_requested"
AFTER = "030_readiness_report_content"


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
    name = f"migration_030_{uuid.uuid4().hex[:8]}"
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


def _seed_report(db: Scratch, tender_id: uuid.UUID, version: int, **extra: str) -> uuid.UUID:
    report_id = uuid.uuid4()
    columns = ", ".join(["id", "tender_id", "tender_state_version", "counts", "blockers", "ready"])
    values = ":id, :tender, :version, '{}'::jsonb, '[]'::jsonb, true"
    for column, value in extra.items():
        columns += f", {column}"
        values += f", '{value}'"
    db.execute(
        f"INSERT INTO readiness_reports ({columns}) VALUES ({values})",
        {"id": report_id, "tender": tender_id, "version": version},
    )
    return report_id


def _constraints(db: Scratch) -> set[str]:
    return {
        row["conname"]
        for row in db.rows(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'readiness_reports'::regclass AND contype = 'u'"
        )
    }


def test_upgrade_backfills_a_legacy_hash_and_moves_the_unique_key(scratch: Scratch):
    tender = uuid.uuid4()
    seeded = [_seed_report(scratch, tender, 1), _seed_report(scratch, tender, 2)]

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    rows = scratch.rows(
        "SELECT id, content_hash, last_generated_at = generated_at AS same_time "
        "FROM readiness_reports"
    )
    assert {row["id"] for row in rows} == set(seeded)
    for row in rows:
        assert row["content_hash"] == f"legacy-{row['id']}", "a value no real hash can equal"
        assert row["same_time"] is True
    assert _constraints(scratch) == {"uq_readiness_report_content"}

    # A second content at one version is allowed; the same content is not.
    _seed_report(scratch, tender, 2, content_hash="abc")
    with pytest.raises(IntegrityError):
        _seed_report(scratch, tender, 2, content_hash="abc")


def test_downgrade_restores_the_old_key_when_each_version_has_one_report(scratch: Scratch):
    tender = uuid.uuid4()
    seeded = [_seed_report(scratch, tender, 1), _seed_report(scratch, tender, 2)]
    command.upgrade(scratch.cfg, AFTER)

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    assert {row["id"] for row in scratch.rows("SELECT id FROM readiness_reports")} == set(seeded)
    columns = {
        row["column_name"]
        for row in scratch.rows(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'readiness_reports'"
        )
    }
    assert "content_hash" not in columns and "last_generated_at" not in columns
    assert _constraints(scratch) == {"uq_readiness_report_version"}


def test_downgrade_refuses_when_a_version_has_more_than_one_report(scratch: Scratch):
    tender = uuid.uuid4()
    _seed_report(scratch, tender, 1)
    command.upgrade(scratch.cfg, AFTER)
    _seed_report(scratch, tender, 1, content_hash="another-content")

    with pytest.raises(RuntimeError, match="refusing to downgrade 030_readiness_report_content"):
        command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == AFTER, "nothing was downgraded"
    assert len(scratch.rows("SELECT id FROM readiness_reports")) == 2, "no report was deleted"
