"""B17 Phase 0: rebuild the whole Neo4j projection from Postgres, tender by
tender, by calling graph_service.project_tender for every tender.

Exists because scripts/rebuild_graph.py is still a 4-line stub that raises
"not implemented" (KNOWN_LIMITATIONS.md, the pytest/Neo4j isolation entry),
and the graph is wiped by any `make test` run and is empty after a Postgres
restore. Projection is deterministic and makes no LLM calls (D3: derived
from relational keys), so this costs nothing to run.

Writes Neo4j (the derived store) and each tender's projection-run rows in
Postgres, exactly as the worker's own project_graph path does. Run it from
the repo root against the stack's published ports:

    POSTGRES_DSN=postgresql+asyncpg://tenderos:changeme@127.0.0.1:5433/tenderos \
    NEO4J_URI=bolt://127.0.0.1:7687 REDIS_URL=redis://127.0.0.1:6379/0 \
    S3_ENDPOINT_URL=http://127.0.0.1:9000 \
    uv run python scripts/project_all_tenders.py

Exits 1 if any tender's projection fails; the others are still attempted.
"""

import asyncio
import sys

from sqlalchemy import text
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service


async def main() -> None:
    async with UnitOfWork() as uow:
        rows = (
            await uow.session.execute(text("select id, name from tenders order by created_at"))
        ).all()
    failures = 0
    for tender_id, name in rows:
        try:
            run = await graph_service.project_tender(tender_id)
            print(f"  [{run.status}] {tender_id}  {name}")
        except Exception as exc:  # report every tender, not just the first failure
            failures += 1
            print(f"  [FAILED] {tender_id}  {name}: {type(exc).__name__}: {exc}")
    print(f"projected {len(rows) - failures}/{len(rows)} tenders")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
