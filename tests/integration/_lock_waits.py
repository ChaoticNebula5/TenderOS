"""Shared steps for tests that order two transactions by row locks, not
sleeps (plan item 59, PR 20). Not a test module (the leading underscore
keeps pytest from collecting it).

The shape every such test follows:
1. The first writer runs until a chosen point inside its transaction and
   stops there (pause_once), holding whatever row locks it has taken.
2. The second writer is started as a task, and the test polls
   pg_stat_activity until the task waits on a lock (waiting), or until it
   finishes without ever waiting (finished_without_waiting).
3. The first writer is released, both finish, and the test reads what was
   stored.

tests/integration/test_tender_row_locks.py (PR 19) keeps its own copies of
the polling helpers; these are the same polls, shared by PR 20's files.
"""

import asyncio
import functools
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import text
from tenderos_backend.db.session import get_session

_SOMEONE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)
_WAITING_QUERIES = text(
    "SELECT query FROM pg_stat_activity "
    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
)


async def someone_waits() -> bool:
    async with get_session() as session:
        return (await session.execute(_SOMEONE_WAITING)).scalar_one() > 0


async def waiting_queries() -> list[str]:
    """The SQL of every session of this database now waiting on a lock."""
    async with get_session() as session:
        return list((await session.execute(_WAITING_QUERIES)).scalars().all())


async def waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until a session waits on a lock, with `task` still
    running; fail with `what` otherwise."""
    for _ in range(200):
        if task.done():
            break
        if await someone_waits():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


async def finished_without_waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s for `task` to finish, failing as soon as any session
    of this database waits on a lock."""
    for _ in range(200):
        if task.done():
            await task
            return
        if await someone_waits():
            raise AssertionError(f"{what}: it waited on a lock")
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: it never finished")


def pause_once(
    monkeypatch: pytest.MonkeyPatch, owner: Any, name: str, *, after: bool
) -> tuple[asyncio.Event, asyncio.Event]:
    """Wrap owner.<name> (a repository class's method or a module's function)
    so that its first call stops inside its caller's transaction, before or
    after the original runs; every later call goes straight through. Returns
    (reached, release): reached is set when it stops, and it goes on once
    release is set. One-shot, so the second writer, which calls the same
    code, is not stopped too."""
    reached, release = asyncio.Event(), asyncio.Event()
    original: Callable[..., Any] = getattr(owner, name)

    @functools.wraps(original)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        if reached.is_set():
            return await original(*args, **kwargs)
        if not after:
            reached.set()
            await release.wait()
            return await original(*args, **kwargs)
        result = await original(*args, **kwargs)
        reached.set()
        await release.wait()
        return result

    monkeypatch.setattr(owner, name, wrapped)
    return reached, release


async def first_then_second(
    first: asyncio.Task, reached: asyncio.Event, release: asyncio.Event, start_second, what: str
) -> tuple[Any, Any, list[str]]:
    """Steps 1 to 3 of the module docstring: wait for `first` to stop, start
    the second writer, wait until it waits on a lock, note the SQL waiting,
    release `first`, and return both outcomes (a result, or the exception
    each raised) and that SQL. Releases and awaits both on every path, so no
    task outlives the test."""
    second: asyncio.Task | None = None
    seen: list[str] = []
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        second = asyncio.create_task(start_second())
        await waiting(second, what)
        seen = await waiting_queries()
    finally:
        release.set()
        pending = [t for t in (first, second) if t is not None]
        outcomes = await asyncio.gather(
            *(asyncio.wait_for(t, timeout=10) for t in pending), return_exceptions=True
        )
    return outcomes[0], outcomes[1], seen
