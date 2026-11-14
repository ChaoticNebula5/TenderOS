"""Prunes LangGraph's own Postgres checkpointer tables (checkpoints,
checkpoint_blobs, checkpoint_writes) - see KNOWN_LIMITATIONS.md's "Unbounded
LangGraph checkpoint growth" entry. Two independent, both-safe categories of
thread are prunable, not one:

1. TERMINAL - workflow_runs.status IN ('completed','failed','cancelled',
   'changes_requested'; the last added by plan item 71).
2. ORPHANED - no workflow_runs row exists at all for this thread_id. Checked
   directly against a live database (240 distinct checkpoint threads, only
   14 workflow_runs rows total, only 1 terminal): 226 of 240 threads are
   orphaned, not merely un-terminal - almost certainly the documented
   test-teardown trap (KNOWN_LIMITATIONS.md's environment-traps entry,
   incidents 3/4/7), which wipes workflow_runs (Base.metadata-managed) while
   leaving checkpoints/checkpoint_blobs/checkpoint_writes (NOT
   Base.metadata-managed, LangGraph's own tables) untouched. An orphaned
   thread is at least as safe to prune as a confirmed-terminal one - there
   is no owning record whose status could still be non-terminal, and
   resume_workflow requires that row to exist at all.

   Guarded rather than trusted outright: only prunes an orphaned thread
   whose newest checkpoint predates the OLDEST surviving workflow_runs row.
   A thread genuinely still in flight (its workflow_runs row just hasn't
   landed yet, for whatever reason) would be more recent than that cutoff
   by construction; anything actually orphaned by a wipe is necessarily
   older than everything the wipe didn't reach. This makes "cannot touch
   something live" structural rather than a bet on how the orphaning
   happened. checkpoint_id has no real timestamp column to compare against
   directly - LangGraph uses UUIDv6 for it specifically because the string
   already sorts chronologically (confirmed: 100% v6 across this database's
   own checkpoints table), so its embedded 60-bit Gregorian-epoch timestamp
   is decoded here instead.

This is BOTH the one-time backfill for existing orphaned/terminal threads
AND the ongoing mechanism - deliberately one idempotent script, not a hook
wired into every status-transition call site in application code. Running
it again later (by hand, or an external cron - this project's locked stack
has no RQ scheduler to wire an in-app one) is the whole mechanism, not just
the initial cleanup.
"""

import argparse
import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from tenderos_backend.db.models.workflow import TERMINAL_RUN_STATUSES
from tenderos_backend.db.session import get_session

# The model's set, not a hand-written copy (plan item 87).
_TERMINAL_STATUSES = TERMINAL_RUN_STATUSES

# Hardcoded, not user input - safe to interpolate directly (SQL identifiers
# can't be bind parameters; this is the standard pattern for iterating a
# fixed, trusted table list).
_CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")

# UUIDv1/v6 timestamps count 100ns intervals since 1582-10-15 (the Gregorian
# calendar reform date, the epoch the original UUID spec chose) rather than
# the Unix epoch - this is the standard offset between the two, in the same
# 100ns units.
_UUID_EPOCH_OFFSET_100NS = 0x01B21DD213814000


def _uuid6_timestamp(value: str) -> datetime:
    """Decodes UUIDv6's embedded 60-bit timestamp. v6 reorders v1's time
    fields (time_high | time_mid | time_low, most-significant first) so the
    string sorts chronologically - Python's stdlib uuid.UUID.time assumes
    v1's own (different) field order and would silently return the wrong
    value here, so this reassembles the bits directly rather than using it.
    """
    n = uuid.UUID(value).int
    time_high = (n >> 96) & 0xFFFFFFFF
    time_mid = (n >> 80) & 0xFFFF
    time_low = (n >> 64) & 0x0FFF
    ts_100ns = (time_high << 28) | (time_mid << 12) | time_low
    unix_100ns = ts_100ns - _UUID_EPOCH_OFFSET_100NS
    return datetime.fromtimestamp(unix_100ns / 10_000_000, tz=UTC)


async def _terminal_thread_ids(session) -> set[str]:
    result = await session.execute(
        text(
            "SELECT langgraph_thread_id FROM workflow_runs "
            "WHERE status = ANY(:statuses) AND langgraph_thread_id IS NOT NULL"
        ),
        {"statuses": list(_TERMINAL_STATUSES)},
    )
    return {row[0] for row in result.all()}


async def _orphaned_thread_ids(session) -> set[str]:
    oldest_surviving = (
        await session.execute(text("SELECT min(created_at) FROM workflow_runs"))
    ).scalar_one_or_none()
    if oldest_surviving is None:
        # No workflow_runs rows exist at all - nothing survived to compare
        # against, so there's no safe cutoff. Treat as "prune nothing" via
        # this category rather than guess a default.
        return set()

    candidates = (
        await session.execute(
            text(
                "SELECT c.thread_id, max(c.checkpoint_id) AS newest_checkpoint_id "
                "FROM checkpoints c "
                "LEFT JOIN workflow_runs wr ON wr.langgraph_thread_id = c.thread_id "
                "WHERE wr.id IS NULL "
                "GROUP BY c.thread_id"
            )
        )
    ).all()

    orphaned: set[str] = set()
    for thread_id, newest_checkpoint_id in candidates:
        if _uuid6_timestamp(newest_checkpoint_id) < oldest_surviving:
            orphaned.add(thread_id)
    return orphaned


async def _report_and_prune(terminal_ids: set[str], orphaned_ids: set[str], execute: bool) -> None:
    all_ids = sorted(terminal_ids | orphaned_ids)
    print(
        f"Terminal threads: {len(terminal_ids)}. Orphaned-and-older-than-the-wipe "
        f"threads: {len(orphaned_ids)}. Combined (deduplicated): {len(all_ids)}."
    )
    if not all_ids:
        print("Nothing prunable.")
        return

    async with get_session() as session:
        for table in _CHECKPOINT_TABLES:
            count = (
                await session.execute(
                    text(f"SELECT count(*) FROM {table} WHERE thread_id = ANY(:ids)"),
                    {"ids": all_ids},
                )
            ).scalar_one()
            print(f"{table}: {count} rows for {len(all_ids)} prunable threads")
            if execute and count:
                await session.execute(
                    text(f"DELETE FROM {table} WHERE thread_id = ANY(:ids)"),
                    {"ids": all_ids},
                )
        if execute:
            await session.commit()
            print("Deleted.")
        else:
            print("Dry run - nothing deleted. Re-run with --execute to actually prune.")


async def _run(execute: bool) -> None:
    # One event loop for the whole run, not several separate asyncio.run()
    # calls - an earlier two-call version broke get_session()'s async engine
    # on Windows (RuntimeError: Event loop is closed), caught on this
    # script's own first real dry-run before it ever reached --execute.
    async with get_session() as session:
        terminal_ids = await _terminal_thread_ids(session)
        orphaned_ids = await _orphaned_thread_ids(session)
    await _report_and_prune(terminal_ids, orphaned_ids, execute)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete rows (default: dry-run, counts only)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.execute))


if __name__ == "__main__":
    main()
