"""Observability phase, PR 30: one request id per api request.

The id is bound into the log context (tenderos_backend.core.log_context) for
the whole request, so every line the request writes carries it, uvicorn's
access line included (uvicorn writes it from inside the response's send,
which runs within this middleware). An outbox event written during the
request stores it (OutboxRepository.create), the dispatcher hands it to the
RQ job in its meta, and the worker binds it again, so the job's lines carry
the same id. It is returned to the caller in the X-Request-ID response
header.

An incoming X-Request-ID is used only if it has the W3C trace-id shape: 32
lowercase hex characters, not all zeros (decision Q1 a's character and
length check; Q7 a's W3C-shaped ids). Anything else, too long, another
alphabet, a UUID with hyphens, a value with spaces or control characters, is
replaced with a new random id, and never logged, so a caller cannot write
text into the logs or the outbox through this header. Nothing in front of
the api sets the header today; a proxy that does at hosting must use the
same shape (nginx's own $request_id has it).

A pure ASGI middleware, not a BaseHTTPMiddleware: it must wrap the response's
send to add the header, and it is added last, so it is the outermost layer
(main.py's "Middleware order"): every response, CORS preflights and the
catch-all's 500 included, carries the header, and every other layer's lines
the id.
"""

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from tenderos_backend.core.log_context import is_valid_request_id, log_context, new_request_id

REQUEST_ID_HEADER = "X-Request-ID"


def accepted_request_id(incoming: str | None) -> str:
    """The incoming id if it has the W3C trace-id shape, else a new one."""
    if incoming is not None and is_valid_request_id(incoming):
        return incoming
    return new_request_id()


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = accepted_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with log_context(request_id=request_id):
            await self.app(scope, receive, send_with_request_id)
