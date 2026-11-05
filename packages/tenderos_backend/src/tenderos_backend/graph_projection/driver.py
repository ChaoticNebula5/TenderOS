from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from neo4j import AsyncDriver, AsyncGraphDatabase
from neo4j import AsyncSession as Neo4jSession

from tenderos_backend.core.config import settings

# Same "module-level singleton, long-lived and pooled" shape as db/session.py's
# own `engine` - the Neo4j driver is designed to be constructed once and reused
# for the process lifetime (it owns its own internal connection pool), not
# recreated per call the way agents/llm adapters are.
#
# B16: explicit timeouts, not library defaults (~30s connection_timeout,
# ~60s connection_acquisition_timeout - KNOWN_LIMITATIONS.md's own "Neo4j
# driver has no explicit request timeout" entry, open since B12). Real
# numbers, not re-guessed: this project's own graph query load test
# (KNOWN_LIMITATIONS.md's "[B16] Load tests" entry) measured real,
# healthy Neo4j round trips at P50=7.9-24.6ms, P95=17.6-37.2ms, P99 up to
# ~290ms on this project's largest real tender - two to three orders of
# magnitude below either number below, so neither bound risks cutting off
# a merely-slow-but-alive query.
#   connection_timeout=5: TCP connect only - a healthy same-Docker-network
#   connect completes in single-digit milliseconds, so 5s is generous
#   headroom for real jitter (a container restart, a brief network blip)
#   while cutting a genuine outage's surfacing time from ~30s to ~5s.
#   connection_acquisition_timeout=10: since driver 6 this spans everything
#   needed to get a working connection, whether waiting on a full pool or
#   opening a new one, so it bounds connection_timeout too. It can
#   legitimately be hit under real concurrent load (several requests touching
#   Neo4j at once), not only an outage, hence more slack than the bound above.
# Worst case before a Neo4j-touching request fails now: 10 s, down from the
# library defaults' 60 s. Measured on driver 6.4.1 (plan item 31): an
# unreachable host fails at 5.0 s with ServiceUnavailable, a full pool at its
# acquisition timeout with ConnectionAcquisitionTimeoutError. Real, not just
# faster on paper: readiness_service.check_freshness's own try/except (B12) is
# what turns a Neo4j failure into a graceful "readiness degraded" blocker
# instead of a crash, but it can only do that once the driver actually gives up.
#
# The tradeoff this shortens, stated rather than left implicit: a
# genuinely slow-but-alive Neo4j under real load (not down, just
# congested) is now more likely to be reported as unreachable than under
# the old, much longer defaults. Chosen deliberately - a fast, honest
# failure that degrades gracefully (per the try/except above) beats a
# request silently hanging for up to a minute before doing the same
# thing.
driver: AsyncDriver = AsyncGraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
    connection_timeout=5,
    connection_acquisition_timeout=10,
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
