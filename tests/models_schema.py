"""The test database's schema, built from the models at the start of every
pytest session (Phase 4 plan item 42).

`create_all()` skips any table that already exists, so a test database left
behind by an Alembic run (the round trip every migration item does) kept
its migrated tables, and the suite silently tested those instead of the
models' (observed in item 40). Here the `public` schema is dropped and
rebuilt instead, every session: the two extensions the migrations create
(`001_initial.py`, `003_documents.py`), then `create_all()`.

Guarded first, on the connection that will run the DROP: the database it is
connected to must end in "_test", and the role must be a superuser. The
name rule is fixed here, not read from the setting that chose the database,
so a setting pointing at the dev database can never empty it.

A plain module, not a conftest, so a unit test can import it without
loading a conftest's fixtures twice. `tests/` is on `sys.path` because
`tests/conftest.py` lives there and the directory has no `__init__.py`
(pytest's default "prepend" import mode).
"""

from sqlalchemy import Connection, text
from tenderos_backend.db.models import Base

TEST_DATABASE_SUFFIX = "_test"

# One round trip, on the caller's own connection. pg_roles, not the
# is_superuser setting: rolsuper is the role's attribute itself.
_WHO_AM_I = text("SELECT current_database(), rolsuper FROM pg_roles WHERE rolname = current_user")


class UnsafeTestDatabaseError(RuntimeError):
    """Raised before anything is dropped."""


def check_safe_to_drop(conn: Connection) -> None:
    database, is_superuser = conn.execute(_WHO_AM_I).one()
    if not database.endswith(TEST_DATABASE_SUFFIX):
        raise UnsafeTestDatabaseError(
            f"refusing to run: connected to database {database!r}, which does not "
            f"end in {TEST_DATABASE_SUFFIX!r}. Every pytest session drops and rebuilds "
            f"the test database's public schema, and every test deletes every row. "
            f"Point POSTGRES_DSN at a database ending in '_test' "
            f"(scripts/test-backend.ps1 and `make test` do). Nothing was dropped."
        )
    if not is_superuser:
        raise UnsafeTestDatabaseError(
            f"refusing to run: the role connected to {database!r} is not a superuser. "
            f"The session start drops the public schema, and only a superuser can "
            f"create the vector extension again afterwards (it is not a trusted "
            f"extension). Nothing was dropped."
        )


def build_from_models(conn: Connection) -> None:
    """The extensions the migrations create, then every model's table, on an
    empty schema. document_chunks.embedding is VECTOR(384), so create_all()
    fails without the vector extension."""
    conn.execute(text("CREATE EXTENSION pgcrypto"))
    conn.execute(text("CREATE EXTENSION vector"))
    Base.metadata.create_all(conn)


def rebuild_from_models(conn: Connection) -> None:
    """Empty the test database and build it from the models. Run inside one
    transaction (Postgres DDL is transactional), so a failure part-way leaves
    the old schema in place."""
    check_safe_to_drop(conn)
    # CASCADE takes everything in the schema with it: the tables, Alembic's
    # alembic_version, LangGraph's checkpoint tables (get_checkpointer()
    # creates them again on first use) and both extensions.
    conn.execute(text("DROP SCHEMA public CASCADE"))
    conn.execute(text("CREATE SCHEMA public"))
    build_from_models(conn)
