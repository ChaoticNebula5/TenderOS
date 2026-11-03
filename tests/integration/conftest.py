import asyncio
import sys

import pytest
import pytest_asyncio
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


@pytest.fixture(scope="session", autouse=True)
def _schema(_models_schema):
    # The guard and the rebuild from the models live in tests/models_schema.py,
    # run once per session by tests/conftest.py's _models_schema (plan item
    # 42). The guard used to be a fixture here with a connection of its own;
    # it now checks the connection that runs the DROP. Why it exists:
    # _clean_tables below deletes every row in every table after each test,
    # and before the guard it wiped a live/dev database seven times
    # (KNOWN_LIMITATIONS.md's environment-traps entry).
    return


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables():
    yield
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
    await engine.dispose()
