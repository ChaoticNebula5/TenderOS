"""PR 28 (observability phase): report what the LLM calls cost and how long
they took, from agent_traces.

The report itself lives in tenderos_backend.domain.services.llm_report; this
is its command line. It answers, for one scope:
- cost per organization, tender, run and agent (unknown where any call has no
  recorded cost, never 0; fake rows marked);
- latency p50, p95 and max per agent and model;
- timeouts and retries per agent and model.

Run it from the repo root against the stack's database:

    POSTGRES_DSN=postgresql+asyncpg://tenderos:...@127.0.0.1:5433/tenderos \
    uv run python scripts/llm_report.py --run RUN_ID
    uv run python scripts/llm_report.py --tender TENDER_ID --json
    uv run python scripts/llm_report.py --org ORG_ID --since 2026-11-01

With no scope it reports every recorded call. It only reads: its
transaction is READ ONLY, and it never prints prompts, outputs or refs (the
report holds none).

Exit status: 0 on a report (an empty one too); 1 on an error, including a
usage error (one line on stderr, no traceback).
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import text
from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.db.session import get_session
from tenderos_backend.domain.services.llm_report import ReportScope, build_report, render_text

EXIT_OK = 0
EXIT_ERROR = 1


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise ValueError(value)
    return number


def _parser() -> _Parser:
    parser = _Parser(prog="llm_report", description="LLM cost and latency from agent_traces.")
    parser.add_argument("--run", type=uuid.UUID, metavar="ID", help="one workflow run")
    parser.add_argument("--tender", type=uuid.UUID, metavar="ID", help="one tender")
    parser.add_argument("--org", type=uuid.UUID, metavar="ID", help="one organization")
    parser.add_argument(
        "--since", type=date.fromisoformat, metavar="YYYY-MM-DD", help="calls from this date (UTC)"
    )
    parser.add_argument(
        "--top", type=_positive, default=20, metavar="N", help="groups per list (default 20)"
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    return parser


async def run(argv: list[str]) -> int:
    """The whole command, minus asyncio.run, so tests can await it."""
    try:
        args = _parser().parse_args(argv)
    except _UsageError as exc:
        print(f"llm_report: {exc}", file=sys.stderr)
        return EXIT_ERROR

    scope = ReportScope(
        workflow_run_id=args.run,
        tender_id=args.tender,
        organization_id=args.org,
        since=datetime(args.since.year, args.since.month, args.since.day, tzinfo=UTC)
        if args.since
        else None,
    )
    try:
        async with get_session() as session:
            # The first statement of the transaction, so the whole report
            # runs read only; the session is closed without a commit.
            await session.execute(text("SET TRANSACTION READ ONLY"))
            report = await build_report(session, scope, top=args.top)
    except Exception as exc:  # one line, no traceback
        print(f"llm_report: error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report), end="")
    return EXIT_OK


def main() -> None:
    # The project's own logging setup, as at the other entry points; nothing
    # is logged on the way, and SQLAlchemy keeps its logger at WARNING.
    configure_logging()
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
