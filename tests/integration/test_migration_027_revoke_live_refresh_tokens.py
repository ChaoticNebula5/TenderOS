"""Migration 027 on a real database at 026 (phase 4 plan item 34, option 2).

Each test builds a scratch database with a real `alembic upgrade` to 026, as
test_migration_025_redteam_target_type.py does, because the session's test
database comes from the models and has no migration history. Its helpers are
copied rather than imported, this codebase's convention for test files.

The seed rows are raw SQL against the schema at 026. Foreign keys are off for
the seed (session_replication_role=replica, which needs a superuser, as the
test server's user is), so no user rows are needed. The CHECK constraints
and the one-live-token-per-family index still apply, so each live row gets
its own family.

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
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BEFORE = "026_outbox_indexes"
AFTER = "027_revoke_live_refresh_tokens"


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

    def tokens(self) -> dict[uuid.UUID, dict]:
        """Every row's state, with timestamps as the database renders them,
        so two snapshots compare exactly."""
        return {
            row["id"]: row
            for row in self.rows(
                "SELECT id, used_at::text AS used_at, revoked_at::text AS revoked_at, "
                "revoked_reason, expires_at::text AS expires_at FROM refresh_tokens"
            )
        }


@pytest.fixture
def scratch(monkeypatch) -> Iterator[Scratch]:
    name = f"migration_027_{uuid.uuid4().hex[:8]}"
    admin_dsn = _sync_dsn(settings.postgres_dsn)
    _execute_as_admin(admin_dsn, f'CREATE DATABASE "{name}"')
    try:
        # env.py reads settings.postgres_dsn and builds an async engine.
        monkeypatch.setattr(
            settings, "postgres_dsn", re.sub(r"/[^/?]+(\?.*)?$", f"/{name}", settings.postgres_dsn)
        )
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, BEFORE)
        yield Scratch(dsn=_sync_dsn(admin_dsn, name), cfg=cfg)
    finally:
        _execute_as_admin(admin_dsn, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


# (name, used, revoked_reason, expires in) for each seeded token.
_SEEDS = [
    ("live", False, None, "7 days"),
    ("live, another family", False, None, "1 hour"),
    ("used", True, None, "7 days"),
    ("revoked at logout", False, "logout", "7 days"),
    ("revoked for reuse", True, "reuse", "7 days"),
    ("superseded", False, "superseded", "7 days"),
    ("expired, never used", False, None, "-1 hour"),
]


def _seed(db: Scratch) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    engine = create_engine(db.dsn)
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL session_replication_role = replica"))
            # Revoked well before the migration, so a revoked_at the
            # migration rewrote would show.
            for name, used, reason, expires_in in _SEEDS:
                ids[name] = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, "
                        "expires_at, used_at, revoked_at, revoked_reason) VALUES "
                        "(:id, gen_random_uuid(), gen_random_uuid(), :hash, "
                        "now() + CAST(:expires_in AS interval), "
                        "CASE WHEN :used THEN now() - interval '1 day' END, "
                        "CASE WHEN CAST(:reason AS text) IS NOT NULL "
                        "THEN now() - interval '1 day' END, :reason)"
                    ),
                    {
                        "id": ids[name],
                        "hash": uuid.uuid4().hex,
                        "expires_in": expires_in,
                        "used": used,
                        "reason": reason,
                    },
                )
    finally:
        engine.dispose()
    return ids


def test_upgrade_revokes_only_live_tokens_with_reason_user(scratch: Scratch):
    ids = _seed(scratch)
    before = scratch.tokens()

    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    after = scratch.tokens()
    live = {ids["live"], ids["live, another family"]}
    for token_id in live:
        assert after[token_id]["revoked_reason"] == "user"
        assert after[token_id]["revoked_at"] is not None
        assert after[token_id]["used_at"] is None
        assert after[token_id]["expires_at"] == before[token_id]["expires_at"]
    for token_id in set(ids.values()) - live:
        assert after[token_id] == before[token_id], token_id
    count = scratch.rows(
        "SELECT count(*) AS n FROM refresh_tokens WHERE used_at IS NULL AND revoked_at IS NULL "
        "AND expires_at > now()"
    )[0]["n"]
    assert count == 0


def test_upgrade_on_an_empty_table_is_a_no_op(scratch: Scratch):
    command.upgrade(scratch.cfg, AFTER)

    assert scratch.version() == AFTER
    assert scratch.tokens() == {}


def test_downgrade_leaves_the_revocations_in_place(scratch: Scratch):
    ids = _seed(scratch)
    command.upgrade(scratch.cfg, AFTER)
    revoked = scratch.tokens()

    command.downgrade(scratch.cfg, BEFORE)

    assert scratch.version() == BEFORE
    assert scratch.tokens() == revoked
    assert scratch.tokens()[ids["live"]]["revoked_reason"] == "user"
