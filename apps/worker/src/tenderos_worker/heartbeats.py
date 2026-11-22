"""Observability PR 31: durable heartbeats for each RQ queue's workers and
for the outbox dispatcher, read by the metrics_exporter.

RQ keeps a heartbeat per worker (`rq:worker:<name>`, last_heartbeat), but a
worker that stops cleanly deletes its own entry, and one that dies has it
expire: either way the last time it was alive disappears, so its age cannot
be shown growing. These keys are written beside RQ's own and never expire,
so "seconds since anything served this queue" keeps growing while nothing
does, the signal a stale-heartbeat alert needs (PR 32). The outbox
dispatcher is not an RQ worker and had no heartbeat at all (design
section 1).

- tenderos:heartbeat:queue:<queue>: written by every worker serving that
  queue, each time RQ heartbeats (worker.LogContextWorkerMixin.heartbeat:
  in its idle loop and while a job runs);
- tenderos:heartbeat:outbox_dispatcher: written after each tick that
  finished, so a dispatcher that is alive but failing every tick
  (Postgres unreachable) ages too.

The value is Unix time in seconds, as text. One key per queue, not per
worker: worker names carry a host and pid and would pile up.
"""

import time
from collections.abc import Callable, Iterable

from redis import Redis
from redis.client import Pipeline

KEY_PREFIX = "tenderos:heartbeat:"
DISPATCHER = "outbox_dispatcher"


def queue_heartbeat_key(queue: str) -> str:
    return f"{KEY_PREFIX}queue:{queue}"


DISPATCHER_HEARTBEAT_KEY = f"{KEY_PREFIX}{DISPATCHER}"


def write_worker_heartbeat(
    connection: Redis | Pipeline, queues: Iterable[str], clock: Callable[[], float] = time.time
) -> None:
    """One SET per queue. On a pipeline the SETs join it, so they go out with
    RQ's own heartbeat commands."""
    stamp = f"{clock():.3f}"
    for queue in queues:
        connection.set(queue_heartbeat_key(queue), stamp)


def write_dispatcher_heartbeat(connection: Redis, clock: Callable[[], float] = time.time) -> None:
    connection.set(DISPATCHER_HEARTBEAT_KEY, f"{clock():.3f}")


def heartbeat_age(raw: bytes | str | None, now: float) -> float | None:
    """Seconds since the stamp, or None when there is none (or it is not a
    number). Never negative: a clock difference between hosts reads as 0."""
    if raw is None:
        return None
    try:
        stamp = float(raw.decode() if isinstance(raw, bytes) else raw)
    except ValueError:
        return None
    return max(0.0, now - stamp)
