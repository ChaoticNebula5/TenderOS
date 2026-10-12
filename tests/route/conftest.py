import asyncio
import logging
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from neo4j import AsyncGraphDatabase
from neo4j import AsyncSession as Neo4jSession
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tenderos_api import main as api_main
from tenderos_api import rate_limit
from tenderos_api.main import app
from tenderos_backend.core.config import settings
from tenderos_backend.db.models import Base
from tenderos_backend.domain.services import graph_service

if sys.platform == "win32":
    # Same reasoning as tests/integration/conftest.py's own comment: LangGraph's
    # AsyncPostgresSaver refuses the default ProactorEventLoop. Route tests
    # don't touch the checkpointer directly, but the app's own dependencies
    # (UnitOfWork, async engines) run under whatever loop policy is active in
    # this process, so this stays consistent with the rest of the suite.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Duplicated from tests/integration/conftest.py rather than imported - pytest's
# default "prepend" import mode adds each conftest-owning directory to
# sys.path individually (neither tests/integration/ nor tests/route/ has an
# __init__.py), so a bare `from tests._x import y` doesn't reliably resolve
# without also changing sys.path/rootdir config. A ~30-line duplication of a
# guard/schema fixture that rarely changes, and fails loudly if it drifts, is
# a smaller risk than fragile cross-directory imports in test infrastructure.
_TEST_DB_SUFFIX = "_test"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _guard_test_database():
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.connect() as conn:
        db_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    await engine.dispose()
    if not db_name.endswith(_TEST_DB_SUFFIX):
        raise RuntimeError(
            f"refusing to run: connected to database {db_name!r}, which does "
            f"not look like a test database (must end in {_TEST_DB_SUFFIX!r}). "
            f"tests/route/ shares the same _clean_tables teardown risk as "
            f"tests/integration/ - point POSTGRES_DSN at a database ending in "
            f"'_test' before running pytest. `make test` does this for you."
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema(_guard_test_database):
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    yield
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _no_pooling_for_the_app_engine():
    """Route tests are the first thing in this project to drive the real
    FastAPI app (tenderos_api.main.app) through TestClient rather than
    calling a service function directly. TestClient(app) is created fresh
    per test (the `client` fixture is function-scoped) and runs the ASGI
    app through its own anyio portal/event loop - but tenderos_backend.
    db.session's `engine` is a module-level singleton created once at
    import time, whose connection pool persists across every test in the
    session. A pooled asyncpg connection is bound to the event loop it was
    created on; reused from a *different* TestClient's loop in a later
    test, asyncpg raises "cannot perform operation: another operation is
    in progress". Disposing the pool between tests doesn't fix it either -
    by teardown time the connection's own loop may already be closed,
    raising "Event loop is closed" instead. Neither is a real bug in the
    app (a real uvicorn process has exactly one event loop for its whole
    life, so pooled connections never cross loops there) - purely a
    TestClient-per-instance-loop artifact.

    Fixed at the root instead of chasing teardown ordering: swap the app's
    engine to NullPool for the test session, so every checkout opens a
    fresh connection and every checkin closes it immediately, on whatever
    loop is asking, with nothing held across requests for a later loop to
    conflict with. Test-side only - the module-level `engine`/
    `async_session_factory` names are monkeypatched, not
    tenderos_backend.db.session's own source.
    """
    import tenderos_backend.db.session as db_session
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    db_session.engine = create_async_engine(settings.postgres_dsn, poolclass=NullPool)
    db_session.async_session_factory = async_sessionmaker(
        db_session.engine, expire_on_commit=False
    )


@asynccontextmanager
async def _get_neo4j_session_no_pooling() -> AsyncIterator[Neo4jSession]:
    # Moved here from test_graph_routes.py once test_redteam_routes.py's
    # own trigger-route test needed the identical fix (check_graph_
    # contradictions, one of redteam's 5 checks, calls graph_service too) -
    # a second file needing this is what makes a shared conftest fixture
    # correct rather than premature; unlike _guard_test_database above,
    # there's no cross-directory import fragility here since both files
    # already live in tests/route/.
    #
    # graph_projection.driver.driver is a module-level singleton whose
    # connection pool binds to whichever event loop first used it.
    # TestClient does not give each TEST its own loop - each individual
    # client.get()/.post() call runs the ASGI app through its own anyio
    # blocking portal, so two calls in the SAME test can each run on a
    # DIFFERENT loop. A single fresh driver swapped in once per test still
    # fails, because the driver's own pool still holds a connection opened
    # on one call's loop and tries to reset it on a later call's loop.
    # The real fix has to match what NullPool does for the Postgres engine:
    # open a brand new driver, use it for exactly one session, close both,
    # on EVERY call, so nothing is ever held across a loop boundary.
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        async with driver.session(database=settings.neo4j_database) as session:
            yield session
    finally:
        await driver.close()


@pytest_asyncio.fixture(autouse=True)
def _no_pooling_for_neo4j(monkeypatch):
    # graph_service.py does `from ...driver import get_session as
    # get_neo4j_session` - a direct function-object import bound into
    # graph_service's OWN namespace at import time. Patching
    # graph_projection.driver.get_session (the original module) does
    # nothing to that already-bound name - graph_service.get_neo4j_session
    # has to be patched directly, since that's the name every one of
    # graph_service.py's own call sites (project_tender, preview_impact,
    # find_contradictions, get_graph_trace) actually resolves.
    monkeypatch.setattr(graph_service, "get_neo4j_session", _get_neo4j_session_no_pooling)


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j(_no_pooling_for_neo4j):
    # Depends on _no_pooling_for_neo4j explicitly (not just declaration
    # order) so the swap always happens before this ever opens a session -
    # same-scope autouse fixture ordering isn't guaranteed otherwise. Runs
    # for every route test, not just graph/redteam ones - a harmless
    # no-op query against an empty graph for tests that never touch Neo4j.
    yield
    async with _get_neo4j_session_no_pooling() as session:
        await session.run("MATCH (n) DETACH DELETE n")


@asynccontextmanager
async def _rate_limit_redis_no_pooling() -> AsyncIterator[AsyncRedis]:
    # Phase 4 blocker 1: rate_limit.py now shares one Redis client for the
    # whole process. Its pooled connections bind to the event loop that
    # opened them, and TestClient runs each request on its own loop (see
    # _get_neo4j_session_no_pooling above), so the shared client would
    # fail on the second request of a test. Same fix as Postgres and
    # Neo4j: a fresh client per request, closed on the way out. Built by
    # rate_limit.build_client so the timeouts and retry policy under test
    # are the real ones.
    redis_client = rate_limit.build_client(api_main.settings.redis_url)
    try:
        yield redis_client
    finally:
        await redis_client.aclose()


@pytest_asyncio.fixture
async def _cleanup_redis():
    # Moved here unchanged from test_rate_limit_middleware.py once
    # test_middleware_order.py also needed it: deletes the ratelimit:* keys
    # a test that re-enabled the limiter created.
    yield
    redis_client = AsyncRedis.from_url(api_main.settings.redis_url)
    async for key in redis_client.scan_iter(match="ratelimit:*"):
        await redis_client.delete(key)
    await redis_client.aclose()


@pytest.fixture
def main_logger_enabled(monkeypatch):
    # For tests that assert on tenderos_api.main's log records. Found by the
    # full suite, not assumed: those tests passed alone and failed after
    # tests/integration/test_schema_diff.py, which runs Alembic in-process.
    # Alembic's env.py calls logging.config.fileConfig(), whose default
    # disable_existing_loggers=True sets `disabled = True` on every logger
    # that already exists, this one included, for the rest of the session.
    # A test-session artifact only: a real API process never runs Alembic.
    # monkeypatch restores whatever state the logger had afterwards.
    monkeypatch.setattr(logging.getLogger("tenderos_api.main"), "disabled", False)


@pytest.fixture(autouse=True)
def _no_pooling_for_rate_limit_redis(monkeypatch):
    # main.py does `from tenderos_api.rate_limit import rate_limit_redis`,
    # binding the function into main's own namespace, so main's name is the
    # one to patch (the same reason _no_pooling_for_neo4j patches
    # graph_service, not the driver module). A no-op for every test that
    # leaves settings.rate_limit_enabled at its test-session default of
    # False, since the limiter then never asks for a client.
    monkeypatch.setattr(api_main, "rate_limit_redis", _rate_limit_redis_no_pooling)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def register_actor(client: TestClient):
    """Factory fixture: real POST /auth/register through the actual route,
    not a fabricated token - route tests exercise the real auth path the
    same way every other test in this project uses real data over mocks.
    The registering user becomes their new organization's ADMIN
    (auth_service.register's own behavior) - sufficient for proving a route
    is reachable and correctly wired; tests asserting a *specific* narrower
    role's access add a membership via POST /organizations/{org_id}/
    memberships separately.

    Returns (headers, user_id, organization_id).
    """

    def _register(
        email: str | None = None, org_name: str = "Route Test Org"
    ) -> tuple[dict[str, str], str, str]:
        email = email or f"{uuid.uuid4()}@example.com"
        response = client.post(
            "/auth/register",
            json={
                "email": email,
                "password": "correct horse battery staple 1",
                "organization_name": org_name,
                "display_name": "Route Test User",
            },
        )
        assert response.status_code == 201, response.text
        body = response.json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        return headers, body["user"]["id"], body["organization_id"]

    return _register


@pytest.fixture
def add_member_with_role(client: TestClient):
    """Factory: adds an EXISTING user (by email) as a member of org_id with
    a specific role, via the real POST /organizations/{org_id}/memberships
    route - acting as whichever caller's headers are passed in (must hold
    manage_users_roles in that org; the org's own founding ADMIN always
    does). This is the mechanism every route test asserting a *narrower*
    role's access/rejection uses - see test_register_actor_and_narrower_
    role_is_actually_rejected for why it's proven correct once here rather
    than trusted by every test that calls it.
    """

    def _add(admin_headers: dict[str, str], org_id: str, member_email: str, role: str) -> dict:
        response = client.post(
            f"/organizations/{org_id}/memberships",
            json={"email": member_email, "role": role},
            headers=admin_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _add
