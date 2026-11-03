"""Phase 4 plan item 42: the guard in front of the test database's rebuild
(tests/models_schema.py).

A recording stand-in for the connection answers the guard's one query with
a chosen database name and superuser flag, and keeps every statement it is
sent, so each test shows exactly what reached the database. The real
query runs against Postgres at the start of every session.
"""

import models_schema
import pytest
from models_schema import UnsafeTestDatabaseError, rebuild_from_models
from tenderos_backend.core.config import settings

_GUARD_QUERY = str(models_schema._WHO_AM_I)


class _Result:
    def __init__(self, row: tuple[str, bool]) -> None:
        self._row = row

    def one(self) -> tuple[str, bool]:
        return self._row


class _RecordingConnection:
    def __init__(self, database: str, is_superuser: bool) -> None:
        self._row = (database, is_superuser)
        self.sent: list[str] = []

    def execute(self, statement) -> _Result:
        self.sent.append(str(statement))
        return _Result(self._row)


@pytest.fixture(autouse=True)
def _record_create_all(monkeypatch):
    # create_all() is recorded, not run: the stand-in is not a database.
    monkeypatch.setattr(
        models_schema.Base.metadata, "create_all", lambda conn: conn.sent.append("create_all()")
    )


@pytest.mark.parametrize("database", ["tenderos", "tenderos_test_old", "tenderos_testing"])
def test_a_database_not_ending_in_test_refuses_and_drops_nothing(database):
    # The setting that chose the database names a test database, as it
    # does in every test run; the connection says otherwise, and the
    # connection is what the guard believes.
    assert settings.postgres_dsn.endswith("_test")
    conn = _RecordingConnection(database, is_superuser=True)

    with pytest.raises(UnsafeTestDatabaseError, match=f"connected to database '{database}'"):
        rebuild_from_models(conn)

    assert conn.sent == [_GUARD_QUERY]


def test_a_role_that_is_not_a_superuser_refuses_and_drops_nothing():
    conn = _RecordingConnection("tenderos_test", is_superuser=False)

    with pytest.raises(UnsafeTestDatabaseError, match="not a superuser"):
        rebuild_from_models(conn)

    assert conn.sent == [_GUARD_QUERY]


def test_a_test_database_and_a_superuser_are_rebuilt_guard_first():
    conn = _RecordingConnection("tenderos_test", is_superuser=True)

    rebuild_from_models(conn)

    assert conn.sent == [
        _GUARD_QUERY,
        "DROP SCHEMA public CASCADE",
        "CREATE SCHEMA public",
        "CREATE EXTENSION pgcrypto",
        "CREATE EXTENSION vector",
        "create_all()",
    ]
