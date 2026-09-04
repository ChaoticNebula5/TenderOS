from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tenderos_backend.core.config import settings

# pool_pre_ping: a connection asyncio-cancelled mid-operation (e.g. by
# LangGraph's own _should_stop_others - see orchestration/graph.py's
# Send()-fan-out siblings and KNOWN_LIMITATIONS.md's sibling-branch-abort
# entry) is left in a broken state by asyncpg, but the pool has no way to
# know that on its own - without pre_ping, that broken connection gets
# handed to the NEXT, completely unrelated query, which fails with a raw
# InterfaceError ("connection is closed") that looks like a fresh bug in
# whatever code happened to draw it next. pre_ping issues a cheap
# liveness check before handing out a pooled connection and transparently
# discards+replaces a dead one - the standard SQLAlchemy remedy for a
# connection invalidated by something outside the pool's own visibility,
# reproduced live via a real cancelled-sibling test before adding this,
# not spec'd from the docs alone.
engine = create_async_engine(settings.postgres_dsn, pool_pre_ping=True)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
