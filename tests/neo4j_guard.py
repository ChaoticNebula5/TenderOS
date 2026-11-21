"""Plan item 100: the run stops unless every Neo4j session lands in the tests'
own database.

15 test files empty the whole graph they reach (`MATCH (n) DETACH DELETE n`),
so the graph must be the tests' own: docker-compose.yml's neo4j_test, a
second server whose one database is tenderos-test (Community edition has one
user database per server). Two checks, like Postgres's _test guard
(models_schema.py): the configured name must end in -test (Neo4j refuses "_"
in a database name), and the server must answer for it. The stack's server
has no -test database, so a run pointed at it stops here, before any test
writes or deletes a node.

This only holds because every session names settings.neo4j_database
(graph_projection/driver.py's get_session and every other call site;
tests/unit/test_neo4j_sessions_name_the_database.py): a session that named no
database would reach a server's default, the stack's real graph.

Its own module, like models_schema.py, so tests can call it directly;
tests/conftest.py's _graph_constraints runs it once per session.
"""

import pytest
from tenderos_backend.core.config import settings
from tenderos_backend.graph_projection.driver import get_session


async def refuse_unless_test_graph() -> None:
    name = settings.neo4j_database
    if not name.endswith("-test"):
        pytest.exit(
            f"refusing to run: NEO4J_DATABASE is {name!r}, which does not end in -test. "
            "Run scripts/test-backend.ps1 (or make test), which point the tests at "
            "their own Neo4j (docker-compose.yml's neo4j_test).",
            returncode=3,
        )
    try:
        async with get_session() as session:
            record = await (await session.run("CALL db.info() YIELD name RETURN name")).single()
    except Exception as exc:  # any failure here means "not the tests' graph"
        pytest.exit(
            f"refusing to run: the Neo4j server at {settings.neo4j_uri} did not answer for "
            f"database {name!r} ({type(exc).__name__}: {exc})",
            returncode=3,
        )
    reached = None if record is None else record["name"]
    if reached != name:
        pytest.exit(f"refusing to run: a session on {name!r} reached {reached!r}", returncode=3)
