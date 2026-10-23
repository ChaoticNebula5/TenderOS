"""Phase 4 item 28: the outbox dispatcher's stop path. In its container the
dispatcher is PID 1, where SIGTERM needs a handler; _run_until_terminated
installs one that sets a stop event. These tests drive the event directly
(add_signal_handler is Linux only, and the suite also runs on Windows):
the tick in progress finishes and _run_forever returns, without waiting
out the poll interval. The live stop is checked on the stack (docker
compose stop outbox_dispatcher, then its log and exit code).

_dispatch_once is replaced, so no database or Redis is touched; the Redis
client _run_forever builds connects lazily and is never used.
"""

import asyncio

import pytest
from tenderos_worker import outbox_dispatcher

pytestmark = pytest.mark.asyncio

# Far longer than the tests' own timeout, so returning in time can only be
# the stop event's doing, never the poll interval running out.
_LONG_POLL_SECONDS = 60
_TEST_TIMEOUT_SECONDS = 5


async def test_a_stop_during_a_tick_ends_the_loop_after_that_tick(monkeypatch):
    stop = asyncio.Event()
    ticks = 0

    async def tick_that_is_asked_to_stop(queues):
        nonlocal ticks
        ticks += 1
        stop.set()

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", tick_that_is_asked_to_stop)
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", _LONG_POLL_SECONDS)

    await asyncio.wait_for(outbox_dispatcher._run_forever(stop), timeout=_TEST_TIMEOUT_SECONDS)
    assert ticks == 1


async def test_a_stop_during_the_wait_between_ticks_ends_the_loop_at_once(monkeypatch):
    stop = asyncio.Event()
    ticks = 0

    async def tick(queues):
        nonlocal ticks
        ticks += 1

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", tick)
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", _LONG_POLL_SECONDS)

    loop_task = asyncio.create_task(outbox_dispatcher._run_forever(stop))
    while ticks == 0:
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(loop_task, timeout=_TEST_TIMEOUT_SECONDS)
    assert ticks == 1
