import asyncio
import sys

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tenderos_backend.core.config import settings
from tenderos_backend.db.models import Base

if sys.platform == "win32":
    # LangGraph's AsyncPostgresSaver (checkpoint.py) uses psycopg's async
    # driver, which refuses to run on Windows' default ProactorEventLoop -
    # only surfaces in tests that actually touch the checkpointer
    # (run_workflow/resume_workflow), not the rest of the suite. Test
    # infrastructure only; production entry points (uvicorn, the RQ worker)
    # aren't affected by this policy since they run inside Linux containers.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_TEST_DB_SUFFIX = "_test"


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _guard_test_database():
    # This suite's own _clean_tables fixture below deletes every row in
    # every table after each test - it has now wiped a live/dev database
    # seven times (KNOWN_LIMITATIONS.md's environment-traps entry) because
    # nothing stopped pytest from running against whatever POSTGRES_DSN
    # happened to be configured. Verified directly against a real
    # connection (SELECT current_database()), not a parse of the DSN
    # string, so a genuinely wrong-looking connection fails loudly here
    # rather than being trusted on the strength of matching config text -
    # see KNOWN_LIMITATIONS.md's own "two-resource guard" standing pattern
    # for why a comparison against an unverified assumption is the wrong
    # shape. This still does not verify _clean_tables' own separately
    # constructed connection resolves identically - see this fixture's own
    # KNOWN_LIMITATIONS entry for that residual.
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.connect() as conn:
        db_name = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    await engine.dispose()
    if not db_name.endswith(_TEST_DB_SUFFIX):
        raise RuntimeError(
            f"refusing to run: connected to database {db_name!r}, which does "
            f"not look like a test database (must end in {_TEST_DB_SUFFIX!r}). "
            f"This suite's _clean_tables fixture deletes every row in every "
            f"table after each test. Point POSTGRES_DSN at a database ending "
            f"in '_test' before running pytest - do not run it against the "
            f"dev/live database. `make test` does this for you."
        )


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema(_guard_test_database):
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        # These tests build the schema from the ORM models rather than by
        # running Alembic, so the extensions the migrations would have
        # created have to be created here too - document_chunks.embedding is
        # a VECTOR(384) column and create_all fails outright without it.
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
