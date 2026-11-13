"""Migration 028 (plan item 34, option 2, review) against a scratch database:
upgrade adds refresh_tokens.resent_at, nullable, with existing rows left as
never re-sent; downgrade drops it; upgrading again adds it back.
test_schema_diff.py separately checks it matches the model's declaration.

Helpers copied from test_migration_026_outbox_indexes.py rather than imported,
this codebase's convention for test files. The seed row is raw SQL with
foreign keys off (session_replication_role=replica), so no user row is
needed."""

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
BEFORE = "027_revoke_live_refresh_tokens"
AFTER = "028_refresh_token_resent_at"


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
    name = f"migration_028_{uuid.uuid4().hex[:8]}"
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


def _column(dsn: str) -> tuple[str, str] | None:
    """(data_type, is_nullable) of refresh_tokens.resent_at, or None."""
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT data_type, is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'refresh_tokens' AND column_name = 'resent_at'"
                )
            ).one_or_none()
            return None if row is None else (row[0], row[1])
    finally:
        engine.dispose()


def _seed_one(dsn: str) -> uuid.UUID:
    token_id = uuid.uuid4()
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, expires_at) "
                    "VALUES (:id, gen_random_uuid(), gen_random_uuid(), :hash, "
                    "now() + interval '7 days')"
                ),
                {"id": token_id, "hash": uuid.uuid4().hex},
            )
    finally:
        engine.dispose()
    return token_id


def test_upgrade_adds_a_nullable_resent_at_and_downgrade_drops_it(scratch):
    cfg, dsn = scratch
    assert _column(dsn) is None
    token_id = _seed_one(dsn)

    command.upgrade(cfg, AFTER)
    assert _column(dsn) == ("timestamp with time zone", "YES")
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            resent = conn.execute(
                text("SELECT resent_at FROM refresh_tokens WHERE id = :id"), {"id": token_id}
            ).scalar_one()
    finally:
        engine.dispose()
    assert resent is None

    command.downgrade(cfg, BEFORE)
    assert _column(dsn) is None

    command.upgrade(cfg, AFTER)
    assert _column(dsn) == ("timestamp with time zone", "YES")
