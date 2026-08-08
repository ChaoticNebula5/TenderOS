from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from tenderos_backend.core.config import settings


@asynccontextmanager
async def get_checkpointer() -> AsyncIterator[BaseCheckpointSaver]:
    """B3's PostgreSQL checkpointer (A3.1, R1's "durable checkpoints")."""

    psycopg_dsn = settings.postgres_dsn.replace("+asyncpg", "")
    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn) as saver:
        await saver.setup()
        yield saver
