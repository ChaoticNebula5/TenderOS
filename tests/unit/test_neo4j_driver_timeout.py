"""graph_projection/driver.py - B16's explicit connection_timeout fix
(KNOWN_LIMITATIONS.md's "Neo4j driver has no explicit request timeout"
entry). Not a config-presence check - a real, timed connection attempt
against an address that will never respond, proving the driver actually
gives up in bounded time rather than the ~30s library default.

Deliberately its own driver instance, not the module-level singleton in
driver.py (which points at the real, healthy test-stack Neo4j and must
stay that way for every other test in this suite) - same kwargs, pointed
at an address this test controls instead.
"""

import time

import pytest
from neo4j import AsyncGraphDatabase
from neo4j.exceptions import ServiceUnavailable

pytestmark = pytest.mark.asyncio

# RFC 5737 TEST-NET-1 - reserved for documentation, never routes anywhere
# real. Packets sent here are silently dropped (no RST, no response),
# which is exactly the "hangs until something gives up" shape a
# connection timeout exists to bound - a closed local port would instead
# fail near-instantly with connection-refused, proving nothing about the
# timeout itself.
_UNREACHABLE_URI = "bolt://192.0.2.1:7687"


async def test_connection_timeout_bounds_a_real_unreachable_host():
    driver = AsyncGraphDatabase.driver(
        _UNREACHABLE_URI,
        auth=("neo4j", "wrong"),
        connection_timeout=5,
        connection_acquisition_timeout=10,
    )
    try:
        start = time.monotonic()
        with pytest.raises(ServiceUnavailable):
            async with driver.session() as session:
                await session.run("RETURN 1")
        elapsed = time.monotonic() - start
        # Generous margin above the 5s connection_timeout for CI/local
        # scheduling jitter - the real claim is "bounded, not the ~30s
        # library default," not "exactly 5.0s to the millisecond."
        assert elapsed < 15, (
            f"expected the driver to give up well under the old ~30s default, "
            f"took {elapsed:.1f}s"
        )
    finally:
        await driver.close()
