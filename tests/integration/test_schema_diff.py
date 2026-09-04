"""Real infrastructure, not a mechanical one-liner - the B16 partial-index
audit's own named recurrence-prevention candidate (KNOWN_LIMITATIONS.md's
USED_IN-adjacent concurrency-gaps section): every pytest integration test
in this project builds its schema via Base.metadata.create_all(), which
only creates what a model's __table_args__ declares. A constraint added
via a standalone op.create_index/op.create_check_constraint/... call in a
migration, with no matching model declaration, is invisible to that
schema and to every test this project has ever run against it - real,
enforced in any environment actually migrated via Alembic, silently
absent everywhere else. Three real instances of exactly this shape were
found and fixed by hand this session (documents' two partial unique
indexes, employees' one) - this test is what stops a fourth from ever
landing unnoticed again.

Builds two REAL, separate scratch databases - one via create_all()
(mirroring every other integration test's own conftest.py fixture
exactly), one via a real `alembic upgrade head` - and diffs their
constraint sets. Deliberately scoped to constraints and unique indexes
(the shapes that represent a real invariant a test could fail to catch),
not plain non-unique performance indexes - the same scope line the
manual audit this test replaces drew explicitly, not an oversight.

Slower than a typical test (creates two real databases and runs the full
migration history) - real infrastructure, not a fast unit check, and
worth the cost for what it closes.
"""

import re
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from tenderos_backend.core.config import settings
from tenderos_backend.db.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Constraint types worth diffing (pg_constraint.contype): primary key,
# unique, check, foreign key. Excludes plain non-unique indexes for the
# same reason the manual audit this test replaces did - they don't
# represent an invariant a test could fail to catch, only a missed
# performance optimization.
_CONSTRAINT_TYPES = ("p", "u", "c", "f")


def _sync_dsn(async_dsn: str, database: str | None = None) -> str:
    dsn = async_dsn.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database is not None:
        dsn = re.sub(r"/[^/?]+(\?.*)?$", f"/{database}", dsn)
    return dsn


def _async_dsn_for_database(async_dsn: str, database: str) -> str:
    return re.sub(r"/[^/?]+(\?.*)?$", f"/{database}", async_dsn)


def _create_scratch_database(admin_sync_dsn: str, name: str) -> None:
    engine = create_engine(admin_sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def _drop_scratch_database(admin_sync_dsn: str, name: str) -> None:
    engine = create_engine(admin_sync_dsn, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as conn:
            # WITH (FORCE) (PG13+) drops even if this test's own engines
            # left an idle connection open - a leaked connection here must
            # not leave scratch databases behind for the next run to trip
            # over on the CREATE DATABASE step.
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        engine.dispose()


def _constraint_names(sync_dsn: str) -> set[tuple[str, str, str]]:
    """(table_name, contype, constraint_name) triples - contype included
    so a same-named object that somehow differs in kind between the two
    builds would still show up as a real difference, not cancel out.
    """
    engine = create_engine(sync_dsn)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT rel.relname, con.contype, con.conname "
                    "FROM pg_constraint con "
                    "JOIN pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                    "WHERE ns.nspname = 'public' AND con.contype = ANY(:types)"
                ),
                {"types": list(_CONSTRAINT_TYPES)},
            ).fetchall()
            unique_index_rows = conn.execute(
                text(
                    "SELECT tablename, 'unique_index', indexname FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexdef LIKE 'CREATE UNIQUE INDEX%'"
                )
            ).fetchall()
        names = {(r[0], r[1], r[2]) for r in rows} | {
            (r[0], r[1], r[2]) for r in unique_index_rows
        }
        # alembic_version is Alembic's own bookkeeping table (tracks which
        # migration a database is at) - it's not part of Base.metadata at
        # all, created only by command.upgrade itself, and legitimately
        # absent from the ORM/create_all() side. Real, expected, not a gap.
        return {row for row in names if row[0] != "alembic_version"}
    finally:
        engine.dispose()


def test_create_all_schema_matches_a_real_migrated_schema(monkeypatch):
    run_id = uuid.uuid4().hex[:8]
    orm_db = f"schema_diff_orm_{run_id}"
    migration_db = f"schema_diff_migration_{run_id}"

    admin_sync_dsn = _sync_dsn(settings.postgres_dsn)
    orm_sync_dsn = _sync_dsn(settings.postgres_dsn, orm_db)
    migration_sync_dsn = _sync_dsn(settings.postgres_dsn, migration_db)
    migration_async_dsn = _async_dsn_for_database(settings.postgres_dsn, migration_db)

    _create_scratch_database(admin_sync_dsn, orm_db)
    _create_scratch_database(admin_sync_dsn, migration_db)
    try:
        # ORM side - same two-extension-then-create_all sequence
        # tests/integration/conftest.py's own _schema fixture uses, so
        # this test's own "orm side" is exactly what every other
        # integration test in this suite actually runs against.
        orm_engine = create_engine(orm_sync_dsn)
        try:
            with orm_engine.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                Base.metadata.create_all(conn)
        finally:
            orm_engine.dispose()

        # Migration side - a real `alembic upgrade head`, not a second
        # create_all() call. env.py reads settings.postgres_dsn itself
        # (db/migrations/env.py:18) and needs an async-capable URL since
        # it builds its own async engine internally - monkeypatched for
        # the duration of this one call, not the sync DSNs used above.
        monkeypatch.setattr(settings, "postgres_dsn", migration_async_dsn)
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")

        orm_constraints = _constraint_names(orm_sync_dsn)
        migration_constraints = _constraint_names(migration_sync_dsn)

        missing_from_orm = migration_constraints - orm_constraints
        extra_in_orm = orm_constraints - migration_constraints

        assert not missing_from_orm and not extra_in_orm, (
            "create_all() and a real `alembic upgrade head` produced different "
            "constraint sets - this is exactly the gap that let 3 real partial "
            "unique indexes stay invisible to every test in this project's history "
            "(KNOWN_LIMITATIONS.md).\n"
            f"In migrations but missing from create_all(): {sorted(missing_from_orm)}\n"
            f"In create_all() but not in migrations: {sorted(extra_in_orm)}"
        )
    finally:
        _drop_scratch_database(admin_sync_dsn, orm_db)
        _drop_scratch_database(admin_sync_dsn, migration_db)
