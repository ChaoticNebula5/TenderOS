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
"""

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork

POLL_INTERVAL_SECONDS = 1.0


async def outbox_event_stream(
    *,
    event_types: Iterable[str],
    payload_key: str,
    resource_id: UUID,
    to_message: Callable[[OutboxEvent], dict[str, Any]],
) -> AsyncIterator[str]:
    """Yield an SSE `data:` line for every new outbox event of `event_types`
    whose payload[payload_key] is `resource_id`, oldest first, and a
    heartbeat comment whenever a poll finds nothing.

    "New" means created after the stream opened. The starting point is the
    database's now(), the same clock that stamps outbox_events.created_at;
    the API host's clock (what B14 used) would hide or replay events by the
    difference between the two clocks (Phase 4 blocker 3's clock gap).
    """
    event_types = list(event_types)
    async with UnitOfWork() as uow:
        since = (await uow.session.execute(select(func.now()))).scalar_one()
    while True:
        async with UnitOfWork() as uow:
            result = await uow.session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type.in_(event_types),
                    OutboxEvent.payload[payload_key].astext == str(resource_id),
                    OutboxEvent.created_at > since,
                )
                .order_by(OutboxEvent.created_at)
            )
            new_events = list(result.scalars().all())

        if not new_events:
            # SSE comment line - ignored by EventSource's own message
            # parsing, but keeps the connection visibly alive against any
            # idle-connection timeout between here and the browser.
            yield ": heartbeat\n\n"
        for event in new_events:
            since = event.created_at
            yield f"data: {json.dumps(to_message(event))}\n\n"

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
