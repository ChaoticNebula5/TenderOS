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


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
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
