from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncSession as Neo4jSession

from tenderos_backend.core.config import settings

# Same "module-level singleton, long-lived and pooled" shape as db/session.py's
# own `engine` - the Neo4j driver is designed to be constructed once and reused
# for the process lifetime (it owns its own internal connection pool), not
# recreated per call the way agents/llm adapters are.
driver: AsyncDriver = AsyncGraphDatabase.driver(
    settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
)

# D1's 11 node labels, each keyed by the natural key that makes MERGE
# idempotent. Tender-scoped labels key on the Postgres row's own UUID (already
# globally unique); shared labels key on knowledge_item_id (D1: "every shared
# node has knowledge_item_id, organization_id").
TENDER_SCOPED_LABELS = (
    "Tender",
    "Requirement",
    "Evidence",
    "Compliance",
    "Role",
    "ProposalClaim",
    "ProposalSection",
)
SHARED_LABELS = ("Employee", "Certification", "Project", "Capability")


@asynccontextmanager
async def get_session() -> AsyncIterator[Neo4jSession]:
    async with driver.session(database=settings.neo4j_database) as session:
        yield session


async def ensure_constraints() -> None:
    """Idempotent - CREATE CONSTRAINT IF NOT EXISTS is safe to call on every
    application startup, same as running Alembic migrations against an
    already-current schema. Called once before the first projection, not on
    every request.
    """
    async with get_session() as session:
        for label in TENDER_SCOPED_LABELS:
            await session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        for label in SHARED_LABELS:
            await session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) "
                "REQUIRE n.knowledge_item_id IS UNIQUE"
            )


async def close_driver() -> None:
    await driver.close()
