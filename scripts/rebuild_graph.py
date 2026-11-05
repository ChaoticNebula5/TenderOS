"""Empty Neo4j and rebuild it from Postgres, the source of truth.

The repair for a graph with duplicated or null keys (phase 4 plan item 49), and
the whole-graph rebuild this stub promised since B10. Stop the app first.

Whole graph, not the affected tenders: a tender can reach a shared node through
USED_IN, FILLS, NAMED_IN or evidence, and one that gets missed keeps a wrong graph
that still reads as fresh, because check_freshness only looks at the completion
marker on its Tender node. 154 local tenders took 31.1 s (measured 2026-11-05).
No LLM calls. Postgres gains one graph_projection_runs row per tender and is
otherwise unchanged. Reads answer 409 GRAPH_STALE until a tender is back.

Constraints go on before anything is projected, so the rebuild cannot recreate
what it just removed.

Exits 0 if every tender projected and the keys are clean, 1 otherwise, 2 without
--yes. Run from the repo root against the stack's published ports, with the
environment scripts/project_all_tenders.py's usage shows:

    uv run python scripts/rebuild_graph.py --yes
"""

import asyncio
import sys

from sqlalchemy import text
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import (
    close_driver,
    ensure_constraints,
    get_session,
)

_WIPE_BATCH = 50_000


async def _wipe_graph() -> int:
    """In batches, so one huge transaction can't run Neo4j out of memory."""
    deleted = 0
    while True:
        async with get_session() as session:
            result = await session.run(
                "MATCH (n) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS c",
                batch=_WIPE_BATCH,
            )
            record = await result.single(strict=True)
        if record["c"] == 0:
            return deleted
        deleted += record["c"]


async def rebuild() -> int:
    print(f"wiped {await _wipe_graph()} nodes")
    await ensure_constraints()
    print("constraints in place")

    async with UnitOfWork() as uow:
        rows = (await uow.session.execute(text("select id from tenders order by created_at"))).all()
    failures = 0
    for (tender_id,) in rows:
        try:
            await graph_service.project_tender(tender_id)
        except Exception as exc:  # report every tender, not just the first failure
            failures += 1
            print(f"  [FAILED] {tender_id}: {type(exc).__name__}: {exc}")
    print(f"projected {len(rows) - failures}/{len(rows)} tenders")

    async with get_session() as session:
        report = await queries.graph_key_report(session)
    print(f"{report.total_nodes} nodes, keys {'clean' if report.clean else 'NOT CLEAN'}")
    return 1 if failures or not report.clean else 0


async def main(argv: list[str]) -> int:
    if argv != ["--yes"]:
        print("this deletes every node in Neo4j; pass --yes to go ahead", file=sys.stderr)
        return 2
    try:
        return await rebuild()
    finally:
        await close_driver()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
