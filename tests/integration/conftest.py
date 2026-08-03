import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from tenderos_backend.core.config import settings
from tenderos_backend.db.models import Base


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    engine = create_async_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
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
