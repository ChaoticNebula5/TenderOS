"""Migration 026 (plan item 55, decision 0082) against a scratch database:
upgrade builds the three outbox indexes CONCURRENTLY and leaves them valid
(a failed concurrent build leaves an INVALID index behind), downgrade drops
them, and upgrading again rebuilds them. test_schema_diff.py separately
checks that they match the models' declarations."""

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "025_redteam_target_type"
AFTER = "026_outbox_indexes"
NAMES = ("idx_outbox_dispatched_at", "idx_outbox_operation_id", "idx_outbox_workflow_run_id")


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
    name = f"migration_026_{uuid.uuid4().hex[:8]}"
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
    """The three item 55 indexes present: name -> (valid, definition)."""
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


def test_upgrade_builds_the_three_indexes_valid_and_downgrade_drops_them(scratch):
    cfg, dsn = scratch
    assert _indexes(dsn) == {}

    command.upgrade(cfg, AFTER)
    built = _indexes(dsn)
    assert sorted(built) == sorted(NAMES)
    assert all(valid for valid, _ in built.values()), built
    assert "((payload ->> 'workflow_run_id'::text))" in built["idx_outbox_workflow_run_id"][1]
    assert "((payload ->> 'operation_id'::text))" in built["idx_outbox_operation_id"][1]
    assert built["idx_outbox_dispatched_at"][1].endswith(
        "(dispatched_at) WHERE (status = 'dispatched'::text)"
    )

    command.downgrade(cfg, BEFORE)
    assert _indexes(dsn) == {}

    command.upgrade(cfg, AFTER)
    assert sorted(_indexes(dsn)) == sorted(NAMES)
