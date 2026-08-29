"""B15: short-lived, single-use tickets for GET .../events/stream.

EventSource cannot send a custom Authorization header, so the SSE route
needs some way for a caller to authenticate via the URL. Until now that was
the real access JWT itself, riding in the query string for its full
jwt_access_minutes (15) life - a real, named exposure (server access logs,
browser history). See KNOWN_LIMITATIONS.md's B14 SSE-auth entry, which
named this ticket approach as the real fix and deferred it to B15.

A ticket is minted by its own endpoint (POST .../events/ticket,
routes/workflow.py), bound to the specific (user_id, run_id) it was issued
for, and consumed exactly once via Redis GETDEL - a leaked ticket is dead
the instant it's used, and even unused it's only live for
_TICKET_TTL_SECONDS, not 15 minutes. Binding to run_id (not just user_id)
means a ticket captured within its short window can only open the one
stream it was minted for, not be replayed against any other run the same
user can see.
"""

import json
import secrets
import uuid

from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.core.config import settings

_TICKET_KEY_PREFIX = "sse_ticket:"
TICKET_TTL_SECONDS = 30


async def mint_sse_ticket(user_id: uuid.UUID, run_id: uuid.UUID) -> str:
    ticket = secrets.token_urlsafe(32)
    value = json.dumps({"user_id": str(user_id), "run_id": str(run_id)})
    async with AsyncRedis.from_url(settings.redis_url) as redis_client:
        await redis_client.set(f"{_TICKET_KEY_PREFIX}{ticket}", value, ex=TICKET_TTL_SECONDS)
    return ticket


async def consume_sse_ticket(ticket: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Returns (user_id, run_id) the ticket was minted for, or None if it
    was never issued, already used, or has expired - all three look
    identical to a caller by design (an attacker probing which is true
    learns nothing from the response).
    """
    async with AsyncRedis.from_url(settings.redis_url) as redis_client:
        raw = await redis_client.getdel(f"{_TICKET_KEY_PREFIX}{ticket}")
    if raw is None:
        return None
    payload = json.loads(raw)
    return uuid.UUID(payload["user_id"]), uuid.UUID(payload["run_id"])
