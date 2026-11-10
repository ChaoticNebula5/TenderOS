"""Server-sent events built from the outbox, for any resource.

Moved out of routes/workflow.py (B14's _workflow_progress_stream) in
Phase 4 so an async operation's progress uses the same mechanism as a
workflow run's. Server-side polling of the outbox at about 1 s, re-emitted
as a client-facing push stream; not database-level push. LISTEN/NOTIFY is
the named path to genuine push if 1 s ever proves inadequate.

Wake-up signal, not payload: each message tells the client that something
changed, and the client re-reads the resource itself (useEventStream's
fetcher) rather than trusting state serialized into the outbox row, which
would be a second copy of the same fact with its own chance to drift.

Plan item 54: "new" means an event this stream has not sent yet, tracked by
id, not "created after the last one sent". created_at is Postgres now(), the
time the writing transaction began, but a row is visible only once that
transaction commits. A cursor on created_at skipped, for good, an event
whose transaction began before a later-stamped one but committed after it
(two specialist branches finishing together), or began before the stream
opened and committed after, or was stamped after the database clock stepped
back (Docker Desktop's VM steps it back about 1 s every ~28 s;
KNOWN_LIMITATIONS.md). Tracking ids depends on none of that: an event is
sent once it is visible, whatever its stamp.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.sql.elements import ColumnElement
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork

POLL_INTERVAL_SECONDS = 1.0


def _matching(
    event_types: list[str], payload_key: str, resource_id: UUID
) -> list[ColumnElement[bool]]:
    return [
        OutboxEvent.event_type.in_(event_types),
        OutboxEvent.payload[payload_key].astext == str(resource_id),
    ]


async def open_outbox_event_stream(
    *,
    event_types: Iterable[str],
    payload_key: str,
    resource_id: UUID,
    to_message: Callable[[OutboxEvent], dict[str, Any]],
) -> AsyncIterator[str]:
    """Read the ids of the matching events that already exist, then return
    the stream of every later one.

    Await this before the response starts (the routes do), so the starting
    set is fixed before the client's EventSource reports the connection
    open; the client re-reads the resource on open (useEventStream.ts), so
    anything in the starting set is covered by that read, and anything that
    becomes visible after it is sent by the stream.
    """
    event_types = list(event_types)
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent.id).where(*_matching(event_types, payload_key, resource_id))
        )
        already_there = set(result.scalars().all())
    return _poll(event_types, payload_key, resource_id, to_message, already_there)


async def _poll(
    event_types: list[str],
    payload_key: str,
    resource_id: UUID,
    to_message: Callable[[OutboxEvent], dict[str, Any]],
    sent: set[UUID],
) -> AsyncIterator[str]:
    """Yield an SSE `data:` line for each matching event not in `sent`,
    oldest stamp first within a poll (for the reader; nothing depends on
    that order), and a heartbeat comment whenever a poll finds nothing.

    Every poll reads the whole outbox table, as the created_at cursor did
    before item 54 (no index covers the payload key): 1.7 ms against 1.2-1.3 ms
    for the cursor on the dev data's 6,639 rows, measured 2026-11-10.
    `sent` holds at most one id per event of one run or operation (34 for
    the largest run in the dev data).
    """
    while True:
        async with UnitOfWork() as uow:
            result = await uow.session.execute(
                select(OutboxEvent)
                .where(
                    *_matching(event_types, payload_key, resource_id),
                    OutboxEvent.id.not_in(sent),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
            )
            new_events = list(result.scalars().all())

        if not new_events:
            # SSE comment line - ignored by EventSource's own message
            # parsing, but keeps the connection visibly alive against any
            # idle-connection timeout between here and the browser.
            yield ": heartbeat\n\n"
        for event in new_events:
            sent.add(event.id)
            yield f"data: {json.dumps(to_message(event))}\n\n"

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
