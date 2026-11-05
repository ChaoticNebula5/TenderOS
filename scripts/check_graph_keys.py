"""Read-only: is every graph node's key present and unique?

Run this once, on any graph holding data, before the first start with Neo4j's
uniqueness constraints (phase 4 plan item 49). Creating a constraint fails on
the first conflict it finds and names one pair; this lists them all, and also
finds null keys, which a uniqueness constraint ignores.

Exits 0 if clean, 1 if anything is wrong, and 2 if the graph is empty, because
an empty graph proves nothing: the backend suite empties the shared Neo4j, so
project it first (scripts/project_all_tenders.py). If anything is found, stop
the app and run scripts/rebuild_graph.py --yes.

Run from the repo root against the stack's published ports:

    NEO4J_URI=bolt://127.0.0.1:7687 uv run python scripts/check_graph_keys.py
"""

import asyncio
import sys

from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import close_driver, get_session


def print_report(report: queries.GraphKeyReport) -> None:
    for c in report.labels:
        print(
            f"  {c.label:16} {c.nodes:7} nodes  "
            f"{c.duplicated_keys:5} duplicated {c.key}  {c.null_keys:5} null"
        )
    for labels, count in report.outside_labels.items():
        print(f"  outside the known labels: {labels} {count} nodes")
    print(f"  {report.total_nodes} nodes in all")


async def run() -> int:
    async with get_session() as session:
        report = await queries.graph_key_report(session)
    print_report(report)
    if report.total_nodes == 0:
        print("EMPTY: this says nothing. Project first: scripts/project_all_tenders.py")
        return 2
    if not report.clean:
        print("NOT CLEAN: stop the app, then scripts/rebuild_graph.py --yes")
        return 1
    print("clean")
    return 0


async def main() -> int:
    try:
        return await run()
    finally:
        await close_driver()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
