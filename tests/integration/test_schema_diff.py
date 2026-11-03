"""Every pytest integration test in this project builds its schema with
Base.metadata.create_all(), which creates only what the models declare.
Anything a migration creates without a matching model declaration (a
partial unique index, a CHECK, a NOT NULL, a server default, a plain
index) is enforced in every database Alembic migrated and absent from the
one every test runs against. Three partial unique indexes were once found
in exactly that state by hand (documents' two, employees' one); this test
stops the next one from landing unnoticed (KNOWN_LIMITATIONS.md).

It builds two separate scratch databases, one with create_all() (the same
build_from_models() every pytest session's rebuild runs, tests/models_schema.py)
and one with a real
`alembic upgrade head`, and compares, read from the Postgres catalog:
- every constraint (primary key, unique, check, foreign key) and its
  definition;
- every index, plain ones included, as its full CREATE INDEX definition
  (name, columns, uniqueness, method, partial predicate);
- every column's type exactly as Postgres reports it, its nullability
  (pg_attribute.attnotnull: Postgres 16 keeps NOT NULL there, not in
  pg_constraint) and its server default as pg_get_expr renders it.

Nothing is normalized: a VARCHAR column on one side and TEXT on the other
is a difference, in the column's type and in any CHECK that mentions it.
Item 40 widened the test from constraints and unique indexes to all four.

Slower than a typical test: it creates two databases and runs the whole
migration history.
"""

import re
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from models_schema import build_from_models
from sqlalchemy import create_engine, text
from tenderos_backend.core.config import settings

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# pg_constraint.contype values compared: primary key, unique, check,
# foreign key. NOT NULL is compared as a column property instead (see
# _COLUMNS_SQL), and indexes through pg_indexes.
_CONSTRAINT_TYPES = ("p", "u", "c", "f")

_CONSTRAINTS_SQL = (
    "SELECT rel.relname, con.contype, con.conname, pg_get_constraintdef(con.oid) "
    "FROM pg_constraint con "
    "JOIN pg_class rel ON rel.oid = con.conrelid "
    "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
    "WHERE ns.nspname = 'public' AND con.contype = ANY(:types)"
)

_INDEXES_SQL = "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"

# Ordinary and partitioned tables only, user columns only (attnum > 0
# skips system columns such as ctid), dropped columns skipped.
_COLUMNS_SQL = (
    "SELECT rel.relname, att.attname, format_type(att.atttypid, att.atttypmod), "
    "att.attnotnull, pg_get_expr(def.adbin, def.adrelid) "
    "FROM pg_attribute att "
    "JOIN pg_class rel ON rel.oid = att.attrelid "
    "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
    "LEFT JOIN pg_attrdef def ON def.adrelid = att.attrelid AND def.adnum = att.attnum "
    "WHERE ns.nspname = 'public' AND rel.relkind IN ('r', 'p') "
    "AND att.attnum > 0 AND NOT att.attisdropped"
)

# Shown in a failure message for something one build has and the other lacks.
_ABSENT = "<absent>"


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


def _schema(sync_dsn: str) -> dict[str, dict[str, str]]:
    """What one database's catalog says, as {aspect: {object: value}}.

    The object is a readable name (`table.column`, `table index name`) so a
    failure message can print it as is; the value is the catalog's own
    rendering, compared as a plain string. alembic_version is Alembic's
    bookkeeping table, created only by `alembic upgrade`, so it is left out.
    """
    engine = create_engine(sync_dsn)
    try:
        with engine.connect() as conn:
            constraints = conn.execute(
                text(_CONSTRAINTS_SQL), {"types": list(_CONSTRAINT_TYPES)}
            ).fetchall()
            indexes = conn.execute(text(_INDEXES_SQL)).fetchall()
            columns = conn.execute(text(_COLUMNS_SQL)).fetchall()
    finally:
        engine.dispose()

    schema: dict[str, dict[str, str]] = {
        "constraint": {},
        "index": {},
        "column type": {},
        "column nullability": {},
        "column default": {},
    }
    for table, contype, name, definition in constraints:
        if table != "alembic_version":
            schema["constraint"][f"{table} {name} ({contype})"] = definition
    for table, name, definition in indexes:
        if table != "alembic_version":
            schema["index"][f"{table} {name}"] = definition
    for table, column, column_type, not_null, default in columns:
        if table == "alembic_version":
            continue
        key = f"{table}.{column}"
        schema["column type"][key] = column_type
        schema["column nullability"][key] = "NOT NULL" if not_null else "NULL"
        schema["column default"][key] = "<none>" if default is None else default
    return schema


def _differences(
    models: dict[str, dict[str, str]], migrations: dict[str, dict[str, str]]
) -> list[str]:
    """One line per object whose value differs between the two builds, or
    that only one build has, with both sides shown."""
    lines = []
    for aspect in models:
        theirs = migrations[aspect]
        ours = models[aspect]
        for key in sorted(ours.keys() | theirs.keys()):
            model_value = ours.get(key, _ABSENT)
            migration_value = theirs.get(key, _ABSENT)
            if model_value != migration_value:
                lines.append(
                    f"{aspect} {key}:\n"
                    f"    models:     {model_value}\n"
                    f"    migrations: {migration_value}"
                )
    return lines


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
        # ORM side - the same build_from_models() the session's rebuild
        # runs (tests/models_schema.py), so this test's own "orm side" is
        # exactly what every other integration test actually runs against.
        # Called directly, not through rebuild_from_models(): a fresh
        # scratch database has nothing to drop, and its name does not end
        # in "_test".
        orm_engine = create_engine(orm_sync_dsn)
        try:
            with orm_engine.begin() as conn:
                build_from_models(conn)
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

        differences = _differences(_schema(orm_sync_dsn), _schema(migration_sync_dsn))

        assert not differences, (
            f"create_all() (the models) and `alembic upgrade head` (the migrations) "
            f"built different schemas, {len(differences)} difference(s). Every "
            f"integration test runs against the models' schema, so a difference here "
            f"is something those tests never see (KNOWN_LIMITATIONS.md).\n" + "\n".join(differences)
        )
    finally:
        _drop_scratch_database(admin_sync_dsn, orm_db)
        _drop_scratch_database(admin_sync_dsn, migration_db)
