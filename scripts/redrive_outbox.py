"""Plan item 20 (decision 0034): re-drive dead-lettered outbox events.

The rule lives in tenderos_backend.domain.services.outbox_redrive; this is
its command line. A re-drive resets the outbox row only (status pending,
attempt_count 0, available_at now()) and the dispatcher delivers it on its
next pass. Each event gets one line: re-driven, would re-drive (dry run) or
refused, with the reason. Payloads are never printed.

Run it from the repo root against the stack's database:

    POSTGRES_DSN=postgresql+asyncpg://tenderos:...@127.0.0.1:5433/tenderos \
    uv run python scripts/redrive_outbox.py --list
    uv run python scripts/redrive_outbox.py --event-id ID [--event-id ID ...]
    uv run python scripts/redrive_outbox.py --event-id ID --apply --reason "parser fix deployed"

Without --apply nothing is written. --apply needs --reason, which each
reset's log line carries.

Exit status: 0 when every requested event was re-driven (or would be, in a
dry run) and for --list; 2 when any was refused; 1 on an error, including a
usage error (argparse's own 2 would read as "refused").
"""

import argparse
import asyncio
import sys
import uuid

from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.domain.services import outbox_redrive
from tenderos_worker.jobs.ingestion import PARSER_VERSION
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _parser() -> _Parser:
    parser = _Parser(prog="redrive_outbox", description="Re-drive dead-lettered outbox events.")
    parser.add_argument("--list", action="store_true", help="list every dead-lettered event")
    parser.add_argument(
        "--event-id",
        action="append",
        default=[],
        type=uuid.UUID,
        metavar="ID",
        help="an event to re-drive (repeatable)",
    )
    parser.add_argument("--apply", action="store_true", help="write the resets (default: dry run)")
    parser.add_argument("--reason", help="why, for the log (required with --apply)")
    return parser


def _parse(argv: list[str]) -> argparse.Namespace:
    args = _parser().parse_args(argv)
    if args.list and args.event_id:
        raise _UsageError("--list and --event-id cannot be combined")
    if not args.list and not args.event_id:
        raise _UsageError("give --list or at least one --event-id")
    if args.apply and args.list:
        raise _UsageError("--list never writes; --apply goes with --event-id")
    if args.apply and not (args.reason and args.reason.strip()):
        raise _UsageError('--apply needs --reason "why"')
    if args.reason and not args.apply:
        raise _UsageError("--reason is only used with --apply")
    return args


async def run(argv: list[str]) -> int:
    """The whole command, minus asyncio.run, so tests can await it."""
    try:
        args = _parse(argv)
    except _UsageError as exc:
        print(f"redrive_outbox: {exc}", file=sys.stderr)
        return EXIT_ERROR

    ctx = outbox_redrive.RedriveContext(parser_version=PARSER_VERSION, max_attempts=MAX_ATTEMPTS)
    try:
        if args.list:
            results = await outbox_redrive.list_dead_lettered(ctx)
        else:
            results = await outbox_redrive.redrive(
                args.event_id, ctx, apply=args.apply, reason=args.reason
            )
    except Exception as exc:  # one line, no traceback (it could quote a row)
        print(f"redrive_outbox: error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_ERROR

    for result in results:
        print(f"{result.event_id} {result.event_type or '-'} {result.status or '-'}: {result.line}")
    if args.list:
        print(f"{len(results)} dead-lettered event(s)")
        return EXIT_OK
    if not args.apply:
        print('dry run: nothing written (--apply --reason "..." to re-drive)')
    return EXIT_REFUSED if any(r.refused for r in results) else EXIT_OK


def main() -> None:
    # The project's own logging setup (INFO, with the redaction filter), as
    # at the api, worker and dispatcher entry points. SQLAlchemy keeps its
    # own logger at WARNING (sqlalchemy/log.py), so no statement is logged.
    configure_logging()
    sys.exit(asyncio.run(run(sys.argv[1:])))


if __name__ == "__main__":
    main()
