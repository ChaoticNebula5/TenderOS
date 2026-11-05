"""`python -m tenderos_backend.graph_projection.apply_constraints`: the compose
`migrate` service's second step, after Alembic.

A module, not a file in scripts/, because scripts/ is not in the API image.
Exits non-zero on any failure, and `migrate` failing keeps every app service
down, as a failed migration does. It never repairs anything: wiping the graph
makes every tender's graph read as stale until the rebuild ends, and someone
has to choose that.
"""

import asyncio
import sys

from neo4j.exceptions import DriverError, Neo4jError

from tenderos_backend.graph_projection.driver import close_driver, ensure_constraints

_ON_FAILURE = (
    "Neo4j constraints could not be applied, so the app services will not start.\n"
    "If this is a duplicate key: list them all with scripts/check_graph_keys.py, then "
    "stop the app and run scripts/rebuild_graph.py --yes. The graph is derived from "
    "Postgres, so nothing is lost.\n"
)


async def run() -> int:
    try:
        await ensure_constraints()
    except (Neo4jError, DriverError) as exc:
        sys.stderr.write(f"{_ON_FAILURE}{type(exc).__name__}: {exc}\n")
        return 1
    finally:
        await close_driver()
    print("Neo4j constraints in place.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
