"""Migration 032 (plan items 94 and 96) against a scratch database: upgrade
creates redteam_passes with its checks and index, every valid row shape is
accepted and every invalid one refused; downgrade drops the table; upgrade
again recreates it. test_schema_diff.py separately checks the upgraded schema
matches the models.

Helpers copied from test_migration_031_document_chunk_origin.py rather than
imported, this codebase's convention for test files. Rows are raw SQL with
foreign keys off (session_replication_role=replica), so no tender, run or
operation rows are needed; CHECK constraints still apply.
"""

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "031_document_chunk_origin"
AFTER = "032_redteam_passes"


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
    name = f"migration_032_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield cfg, _sync_dsn(admin_dsn, name)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


_INSERT = (
    "INSERT INTO redteam_passes (tender_id, outcome, graph_checks, graph_state_version, "
    "error_code) VALUES (:tender, :outcome, :checks, :version, :code)"
)


def _insert(dsn: str, outcome: str, checks: str | None, version: int | None, code: str | None):
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(_INSERT),
                {
                    "tender": str(uuid.uuid4()),
                    "outcome": outcome,
                    "checks": checks,
                    "version": version,
                    "code": code,
                },
            )
    finally:
        engine.dispose()


def _rows(dsn: str, query: str) -> list[tuple]:
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            return [tuple(row) for row in conn.execute(text(query)).all()]
    finally:
        engine.dispose()


def _table_exists(dsn: str) -> bool:
    return _rows(dsn, "SELECT to_regclass('public.redteam_passes') IS NOT NULL")[0][0]


VALID = [
    ("completed", "ran", 7, None),
    ("completed", "skipped", None, None),
    ("failed", None, None, "LLMTimeoutError"),
]
INVALID = [
    # a completed pass must say whether its graph checks ran
    ("completed", None, None, None),
    # a completed pass carries no error code
    ("completed", "ran", 7, "RuntimeError"),
    # a failed pass says why, and nothing about graph checks
    ("failed", None, None, None),
    ("failed", "ran", 7, "RuntimeError"),
    # a version exactly when the graph checks ran
    ("completed", "ran", None, None),
    ("completed", "skipped", 7, None),
    ("failed", None, 7, "RuntimeError"),
    # unknown values
    ("partial", "ran", 7, None),
    ("completed", "maybe", None, None),
]


def test_upgrade_creates_the_table_and_its_checks_and_downgrade_drops_it(scratch):
    cfg, dsn = scratch
    assert not _table_exists(dsn)

    command.upgrade(cfg, AFTER)
    assert _table_exists(dsn)
    assert _rows(
        dsn, "SELECT indexname FROM pg_indexes WHERE tablename = 'redteam_passes' ORDER BY 1"
    ) == [("idx_redteam_pass_latest",), ("redteam_passes_pkey",)]

    for outcome, checks, version, code in VALID:
        _insert(dsn, outcome, checks, version, code)
    for outcome, checks, version, code in INVALID:
        with pytest.raises(IntegrityError):
            _insert(dsn, outcome, checks, version, code)
    assert _rows(dsn, "SELECT count(*) FROM redteam_passes") == [(len(VALID),)]
    # finished_at and id fill themselves.
    assert _rows(
        dsn, "SELECT count(*) FROM redteam_passes WHERE finished_at IS NOT NULL AND id IS NOT NULL"
    ) == [(len(VALID),)]

    command.downgrade(cfg, BEFORE)
    assert not _table_exists(dsn)

    command.upgrade(cfg, AFTER)
    assert _table_exists(dsn)
    assert _rows(dsn, "SELECT count(*) FROM redteam_passes") == [(0,)]
