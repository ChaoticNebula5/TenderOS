"""Observability PR 31: count and time every api request (decision Q3 a).

Labelled by the matched route's template (`/tenders/{tender_id}`), never by
the path itself, so ids never become label values. The router writes the
matched route into the request's scope (FastAPI's APIRoute.matches sets
scope["route"]) and every layer passes that same scope dict down, so it is
read here after the response. A path no route matches (a 404 from the
router) carries no route and is counted under UNMATCHED_ROUTE: a scan of a
thousand random paths adds one label value, not a thousand. A response a
middleware inside this one gives before the router runs (the rate limiter's
429 and login's 503, the body limit's 413, CORS's preflight answer) has no
route either and is counted there too, under its own code. A method
outside the fixed list is "other", and so is a status outside 100 to 599;
any other status is counted under its exact code (core/metrics.py says why).

A pure ASGI middleware, added just inside RequestIdMiddleware (main.py's
"Middleware order"), so it sees every response the app sends: a CORS
preflight, a 429, the catch-all's 500. An exception that still escapes
(ServerErrorMiddleware turns it into a 500 outside this layer) is counted as
500 and raised again. The duration ends at the last body chunk sent, so a
streaming response is timed for as long as it stayed open.
"""

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from tenderos_backend.core.metrics import (
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS,
    UNMATCHED_ROUTE,
    method_label,
    status_code_label,
)


def route_label(scope: Scope) -> str:
    """The matched route's template, or UNMATCHED_ROUTE."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else UNMATCHED_ROUTE


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def send_recording_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_recording_status)
        finally:
            method = method_label(scope["method"])
            route = route_label(scope)
            HTTP_REQUESTS.labels(route, method, status_code_label(status_code)).inc()
            HTTP_REQUEST_DURATION.labels(route, method).observe(time.perf_counter() - started)
