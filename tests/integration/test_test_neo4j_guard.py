"""Plan item 100: the backend tests run on their own Neo4j, and stop otherwise.

tests/neo4j_guard.py's refuse_unless_test_graph runs once per session (from
tests/conftest.py's _graph_constraints). Here it is called directly against the
real test server, with the configured database name swapped per test.
"""

import pytest
from neo4j_guard import refuse_unless_test_graph
from tenderos_backend.core.config import settings
from tenderos_backend.graph_projection.driver import get_session

pytestmark = pytest.mark.asyncio


async def test_the_suite_reaches_the_tests_own_database():
    async with get_session() as session:
        record = await (await session.run("CALL db.info() YIELD name RETURN name")).single()

    assert record is not None
    assert record["name"] == settings.neo4j_database
    assert settings.neo4j_database.endswith("-test")


async def test_the_configured_database_passes():
    assert await refuse_unless_test_graph() is None


async def test_a_name_not_ending_in_test_stops_the_run_before_any_session(monkeypatch):
    # The stack's own database name. No session may be opened to find out.
    monkeypatch.setattr(settings, "neo4j_database", "neo4j")
    opened = []
    monkeypatch.setattr("neo4j_guard.get_session", lambda: opened.append(1))

    with pytest.raises(pytest.exit.Exception) as stopped:
        await refuse_unless_test_graph()

    assert "'neo4j', which does not end in -test" in str(stopped.value)
    assert opened == []


async def test_a_server_without_that_database_stops_the_run(monkeypatch):
    # What a run pointed at the stack's server meets: a -test name the server
    # does not have. The session is real; the server refuses it.
    monkeypatch.setattr(settings, "neo4j_database", "absent-test")

    with pytest.raises(pytest.exit.Exception) as stopped:
        await refuse_unless_test_graph()

    message = str(stopped.value)
    assert "did not answer for database 'absent-test'" in message
    assert "DatabaseNotFound" in message
