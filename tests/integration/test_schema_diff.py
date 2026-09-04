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


def _normalize_definition(definition: str) -> str:
    """Strips a real, pre-existing, whole-schema rendering difference
    that isn't a real invariant difference: every migration file
    declares its enum-like string columns as `Text`, while the ORM
    models declare the same columns with a bare `str` type hint, which
    SQLAlchemy 2's own default type map renders as `VARCHAR` - Postgres
    then renders the identical CHECK constraint differently depending on
    which one the column actually is (`status = ANY(ARRAY[...text])` vs
    `(status)::text = ANY((ARRAY[...varchar])::text[])`), even though
    unbounded VARCHAR and TEXT enforce identically in Postgres - no real
    difference in what's actually allowed. Found live, not assumed: this
    exact pattern showed up on all 32 CHECK constraints in the schema the
    first time this test compared full definitions instead of just
    names, none of them a real drift - normalizing it away here keeps
    this test focused on genuine definitional differences (different
    allowed values, different predicates, different columns), not this
    cosmetic one. That whole-schema VARCHAR/TEXT mismatch is itself real
    and pre-existing (not introduced this session) - named in
    KNOWN_LIMITATIONS.md, not silently fixed by widening this test's own
    scope beyond what CodeRabbit actually asked for.
    """
    normalized = definition.replace("::character varying", "::text")
    normalized = re.sub(r"\(([\w.]+)\)::text\b", r"\1", normalized)
    # (ARRAY[...])::text[] -> ARRAY[...] - the cast trails the closing
    # paren of the array literal, not sits inside it; a first attempt at
    # this pattern put ::text[] inside the capture group's own closing
    # paren, which never matched the real Postgres output at all (caught
    # by actually rerunning the test against real Postgres, not assumed
    # correct from writing the regex).
    normalized = re.sub(r"\((ARRAY\[[^\]]*\])\)::text\[\]", r"\1", normalized)
    return normalized


def _constraint_names(sync_dsn: str) -> set[tuple[str, str, str, str]]:
    """(table_name, contype, constraint_name, definition) 4-tuples -
    contype included so a same-named object that somehow differs in kind
    between the two builds would still show up as a real difference, not
    cancel out. The definition (CodeRabbit-caught gap: this used to be a
    3-tuple with no definition at all) is what actually catches drift
    under a matching name - a partial unique index or CHECK constraint
    with the same name but different indexed columns/WHERE predicate/
    allowed values passed this test before, silently, which defeats the
    whole point: this session alone twice widened a CHECK constraint's
    own allowed-values list (ck_approval_gate_type, ck_proposal_claim_
    kind's sibling additions) - exactly the shape a name-only compare
    cannot see.
    """
    engine = create_engine(sync_dsn)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT rel.relname, con.contype, con.conname, "
                    "pg_get_constraintdef(con.oid) "
                    "FROM pg_constraint con "
                    "JOIN pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
                    "WHERE ns.nspname = 'public' AND con.contype = ANY(:types)"
                ),
                {"types": list(_CONSTRAINT_TYPES)},
            ).fetchall()
            unique_index_rows = conn.execute(
                text(
                    "SELECT tablename, 'unique_index', indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexdef LIKE 'CREATE UNIQUE INDEX%'"
                )
            ).fetchall()
        names = {(r[0], r[1], r[2], _normalize_definition(r[3])) for r in rows} | {
            (r[0], r[1], r[2], _normalize_definition(r[3])) for r in unique_index_rows
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
