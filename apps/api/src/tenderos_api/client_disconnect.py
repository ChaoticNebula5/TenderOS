"""A client that goes away before its request body is read.

The browser cancels a request still in flight when the page navigates or
reloads. If that happens before the API has read the body, Starlette raises
ClientDisconnect at the read. That is the client's choice, not a server
fault, so it is logged as one INFO line naming the method and path (never
the query string: the SSE route carries a live token there) and answered
with 499, nginx's "client closed request", so nothing that later counts 5xx
responses counts it. The client never receives the answer.

Two places read a body. FastAPI reads it before a route with a body
parameter runs and turns a failed read into HTTPException(400), raised from
the ClientDisconnect (fastapi/routing.py); errors.py's HTTPException handler
recognises that cause. idempotency.py reads it for routes without one, and
there the ClientDisconnect itself reaches main.py's catch-all:
BaseHTTPMiddleware re-raises the exception from inside its task group, so
the ExceptionGroup in main's CI log (run 40116721087) is only the context
printed beside it. The read happens before the idempotency key is claimed,
so a disconnect there leaves no record behind.
"""

import logging

from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

CLIENT_CLOSED_REQUEST = 499


def is_client_disconnect(exc: BaseException) -> bool:
    return isinstance(exc, ClientDisconnect)


def client_closed_response(request: Request) -> Response:
    logger.info(
        "client disconnected before its request was read: %s %s",
        request.method,
        request.url.path,
    )
    return Response(status_code=CLIENT_CLOSED_REQUEST)
