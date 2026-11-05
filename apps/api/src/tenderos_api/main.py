import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import boto3
import redis.asyncio as redis
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase
from redis.asyncio import Redis as AsyncRedis
from starlette.middleware.base import BaseHTTPMiddleware
from tenderos_backend.agents.llm.factory import log_llm_provider
from tenderos_backend.agents.llm.fake_fallbacks import install_fake_fallback_responses
from tenderos_backend.core.config import settings as backend_settings
from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.graph_projection.driver import close_driver

from tenderos_api.client_disconnect import client_closed_response, is_client_disconnect
from tenderos_api.config import settings
from tenderos_api.errors import (
    APIError,
    InternalServerError,
    LoginUnavailableError,
    error_response,
    register_exception_handlers,
)
from tenderos_api.logging_filters import install_access_log_redaction
from tenderos_api.rate_limit import (
    GLOBAL_LIMIT,
    GLOBAL_WINDOW_SECONDS,
    LOGIN_LIMIT,
    LOGIN_PATH,
    LOGIN_UNAVAILABLE_RETRY_AFTER_SECONDS,
    LOGIN_WINDOW_SECONDS,
    REDIS_UNAVAILABLE_ERRORS,
    check_rate_limit,
    client_key,
    rate_limit_redis,
    redis_outage_log,
)
from tenderos_api.routes import (
    auth,
    commercial,
    compliance,
    documents,
    gates,
    graph,
    knowledge,
    operations,
    organizations,
    proposal,
    readiness,
    redteam,
    requirements,
    talent,
    tenders,
    workflow,
)

# B16: real, process-wide (this module loads once per uvicorn process), so
# this covers every route/service call this process ever makes - see
# log_redaction.py's own module docstring for what this does and doesn't
# cover. Must run before anything below logs anything, and before the
# route modules import (each does logging.getLogger(__name__) at import
# time, but the level/handler configuration this call provides is global
# and doesn't care about import order beyond "before the process actually
# starts serving").
configure_logging()
# Phase 4 plan item 3: SSE tickets out of uvicorn's access log. See
# logging_filters.py for why import time is after uvicorn's own setup.
install_access_log_redaction()
# B17: a real no-op unless LLM_PROVIDER=fake (see that function's own
# docstring) - installed at the same three entrypoints configure_logging()
# already documents, right after it, so a fake-provider process is fully
# ready to answer before it starts serving.
install_fake_fallback_responses()
# Phase 4 plan item 46: which provider this process is on, once at start.
log_llm_provider()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Phase 4 plan item 31: nothing closed graph_projection/driver.py's shared
    # driver. Neither driver version closes an async driver when it is
    # collected, so this is the one place its pool gets a clean goodbye.
    # A closed driver cannot be reused (a DriverError since driver 6), so a
    # test entering `with TestClient(app)` must patch close_driver, or every
    # later test using the shared driver fails.
    yield
    await close_driver()


app = FastAPI(title="TenderOS API", lifespan=_lifespan)
# Every middleware is registered in ONE block further down ("Middleware
# order"), after all of them are defined, so the order is read in one
# place instead of being implied by where each definition sits.

# B15: secure headers - nothing set these before. This is a pure JSON API
# (no HTML views of its own) except for FastAPI's auto-generated /docs and
# /redoc, which need to load their own JS/CSS - a blanket
# `default-src 'none'` CSP would break Swagger UI, so those two paths (plus
# the /openapi.json they both fetch) are exempt from CSP specifically, not
# from the other headers below, which are safe everywhere regardless of
# content type.
_CSP_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json"}


async def _security_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # Every route in this app is Bearer-token-authenticated with no cookie
    # session, so no CSRF-relevant Referer information needs to travel at
    # all - and this app also has a real, accepted case (the SSE stream)
    # where a live token currently rides in the URL query string, which
    # Referer would otherwise leak to any cross-origin resource a response
    # page might load.
    response.headers["Referrer-Policy"] = "no-referrer"
    # Harmless to set unconditionally - browsers only honor HSTS over a
    # real HTTPS connection, so this has no effect on local HTTP dev/test
    # traffic.
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path not in _CSP_EXEMPT_PATHS:
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


# B15 CodeRabbit finding: FastAPI/Starlette parse the full request body -
# including a multipart file upload - INSIDE routing, resolving `File(...)`
# into an UploadFile before any route handler code runs at all. That means
# documents.py/knowledge.py's own _read_bounded (which reads in capped
# chunks and rejects the instant a limit is crossed) never gets a chance to
# act until AFTER Starlette has already spooled the entire body - into
# memory, then to a temp file past SpooledTemporaryFile's threshold - for
# an upload of any size, including one with no limit anywhere. _read_
# bounded's own "never materializes an unbounded upload" claim only holds
# once control reaches it, which is already too late for an oversized
# request; this middleware is the actual fix for that gap.
#
# Checking Content-Length here, before call_next (and therefore before
# routing/multipart-parsing ever starts), rejects the HONEST case with zero
# body bytes read: every real browser and HTTP client sends an accurate
# Content-Length for a request built from an in-memory/on-disk file.
# Reuses max_upload_size_mb (tenderos_backend.core.config, not this app's
# own Settings - documents.py's _read_bounded enforces the identical
# ceiling for the same reason) as a blanket per-request cap, not just an
# upload-route-specific one: every other route's real request bodies are
# far smaller than 50MB anyway, so the same ceiling is a harmless general
# backstop, not a new, separately-tuned limit.
#
# Residual gap, on record rather than silently closed: a client that LIES
# about Content-Length (declares it small, sends more; or omits it
# entirely via chunked transfer-encoding) is not caught here - catching
# that would mean inspecting the ASGI receive() stream's actual byte count
# as it arrives, a materially larger change than a header check. See
# KNOWN_LIMITATIONS.md.
_MAX_BODY_BYTES = backend_settings.max_upload_size_mb * 1024 * 1024


async def _max_body_size(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_bytes = int(content_length)
        except ValueError:
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > _MAX_BODY_BYTES:
            return error_response(
                APIError(
                    413,
                    "PAYLOAD_TOO_LARGE",
                    f"Request body exceeds the {backend_settings.max_upload_size_mb} MB limit",
                )
            )
    return await call_next(request)


# B16: header name/value are not secrets - Settings.e2e_rate_limit_
# bypass_enabled is the only real gate (see its own comment), so these
# don't need to be guessable-but-safe, just unambiguous and self-
# documenting.
_E2E_BYPASS_HEADER = "x-tenderos-e2e-bypass"
_E2E_BYPASS_VALUE = "playwright"


async def _limit_rejection(redis_client: AsyncRedis, ip: str, path: str) -> Response | None:
    """The 429 to return, or None if the request is within both limits.

    Split out of _rate_limit so every Redis call sits inside one try block
    there, and so call_next is never inside that block.
    """
    allowed, retry_after = await check_rate_limit(
        redis_client, f"ratelimit:global:{ip}", GLOBAL_LIMIT, GLOBAL_WINDOW_SECONDS
    )
    if not allowed:
        return error_response(
            APIError(429, "RATE_LIMITED", "Too many requests."),
            headers={"Retry-After": str(retry_after)},
        )

    # A tighter, dedicated limit on the one route a credential-stuffing/
    # brute-force attempt would actually hit - checked in addition to,
    # not instead of, the global limit above.
    if path == LOGIN_PATH:
        allowed, retry_after = await check_rate_limit(
            redis_client, f"ratelimit:login:{ip}", LOGIN_LIMIT, LOGIN_WINDOW_SECONDS
        )
        if not allowed:
            return error_response(
                APIError(429, "RATE_LIMITED", "Too many login attempts."),
                headers={"Retry-After": str(retry_after)},
            )
    return None


async def _rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if not settings.rate_limit_enabled:
        return await call_next(request)
    # B16: CORS preflight OPTIONS requests are exempt unconditionally, in
    # production too, not just under the E2E bypass below - correction to
    # this entry's own earlier state, which counted them. Two things,
    # checked directly rather than assumed either way: (1) confirmed this
    # middleware DOES see preflights (CORSMiddleware does not short-
    # circuit them before reaching here in this app's actual behavior,
    # whatever the general framework docs might suggest - a live curl
    # test drove 120 raw OPTIONS requests and got real 429s past request
    # ~100); (2) a live E2E run then hit exactly that on a request the
    # bypass header SHOULD have covered - real browser preflights are
    # browser-internal negotiation, not something page-JS or even
    # Playwright's extraHTTPHeaders is guaranteed to reach the same way
    # it reaches the real follow-up request, so the bypass's own
    # reliability on preflights specifically was never actually solid
    # ground. Exempting OPTIONS outright removes the question rather than
    # resolving it, and is independently correct on its own merits: a
    # preflight carries no Authorization header and does no real work -
    # counting it toward a limit meant to protect against abusive real
    # traffic was arguably wrong before B16 ever touched this file, not
    # only under test.
    #
    # Phase 4 blocker 1: point (1) above was true only because this
    # middleware used to sit OUTSIDE CORSMiddleware. With CORS now
    # outermost (see "Middleware order" below), CORSMiddleware answers a
    # real preflight itself and this middleware never sees it. The
    # exemption stays for OPTIONS requests that are not preflights (no
    # Origin or no Access-Control-Request-Method), which CORSMiddleware
    # passes through.
    if request.method == "OPTIONS":
        return await call_next(request)
    # B16: E2E harness only, for everything else. This spec drives real,
    # sequential mutations at a request rate no human produces (a real run
    # measured 41+ real PATCH /requirements calls inside one minute -
    # enough on its own to trip this exact limiter) - B15's limiter is
    # deliberately built for human/abusive-bot traffic, not a scripted
    # test harness, and pacing a harness down to look human tests the
    # harness's own patience, not anything the product does. The env-var-
    # plus-header gate (not header alone) is what keeps this from ever
    # being reachable in a real deployment - see Settings.
    # e2e_rate_limit_bypass_enabled's own comment for the full contract.
    if (
        settings.e2e_rate_limit_bypass_enabled
        and request.headers.get(_E2E_BYPASS_HEADER) == _E2E_BYPASS_VALUE
    ):
        return await call_next(request)

    ip = client_key(request.client.host if request.client else None)

    # Phase 4 blocker 1: one shared client (rate_limit.py) instead of a new
    # pool per request, and fail open. If Redis cannot be reached or does
    # not answer within rate_limit.py's timeouts, the request is allowed.
    # A failure on the global check skips the login check too, since both
    # use the same unavailable Redis. Only the exception TYPE is logged.
    #
    # Phase 4 plan item 2 (decision 0041): login is the exception. While
    # Redis is unavailable, POST /auth/login gets a 503 with Retry-After,
    # whichever of its two checks failed. The 503 is returned here, inside
    # CORSMiddleware and _security_headers, so it carries both header sets
    # like the 429. The WARNING goes through redis_outage_log: the first
    # failure, then at most one line per interval with a count, not one
    # line per request.
    #
    # call_next stays outside the try on purpose. A route that itself uses
    # Redis (sse_tickets.py) can raise the same ConnectionError; inside the
    # try, that would be caught here, logged as a limiter problem, and the
    # route would be called a second time.
    try:
        async with rate_limit_redis() as redis_client:
            rejection = await _limit_rejection(redis_client, ip, request.url.path)
    except REDIS_UNAVAILABLE_ERRORS as exc:
        covered = redis_outage_log.record()
        if covered is not None:
            logger.warning(
                "rate limiter skipped, Redis unavailable (%s); %d request(s) since the last "
                "such line; login refused with 503, other routes allowed",
                type(exc).__name__,
                covered,
            )
        if request.url.path == LOGIN_PATH:
            return error_response(LoginUnavailableError(LOGIN_UNAVAILABLE_RETRY_AFTER_SECONDS))
        rejection = None

    if rejection is not None:
        return rejection
    return await call_next(request)


async def _catch_unhandled_exceptions(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Phase 4 blocker 1: turn any exception nothing else handled into the
    same JSON envelope every APIError uses, as a 500 INTERNAL_ERROR.

    Without this, an unhandled exception reaches Starlette's own
    ServerErrorMiddleware, which is always the outermost layer, OUTSIDE
    CORSMiddleware: its plain-text 500 carries no CORS headers, so the
    browser hides it and the web app sees a generic network failure. This
    middleware sits inside CORS (see "Middleware order"), so its 500 gets
    the CORS and security headers like any other response.

    The traceback goes to the log; the body carries no internal detail.
    Only the method and path are logged, not the full URL: the SSE route
    carries a live token in its query string.

    A client that disconnected before its body was read is not an error:
    one INFO line and a 499 (client_disconnect.py).
    """
    try:
        return await call_next(request)
    except Exception as exc:
        if is_client_disconnect(exc):
            return client_closed_response(request)
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(InternalServerError())


# Middleware order. Outermost first, this is what a request passes through:
#
#   ServerErrorMiddleware        Starlette's own, always outermost
#   CORSMiddleware
#   _security_headers
#   _catch_unhandled_exceptions
#   _rate_limit
#   _max_body_size
#   ExceptionMiddleware          Starlette's own, always innermost; runs the
#                                APIError / HTTPException / 422 handlers
#   router
#
# add_middleware() INSERTS AT INDEX 0 of app.user_middleware (Starlette's
# applications.py), and the stack is built from that list with index 0 as
# the outermost layer. So the LAST middleware added is the OUTERMOST, and
# the calls below run in the reverse of the list above. (B15's comment
# here said the opposite, "the first one added is outermost"; that is why
# the 429 used to skip CORS and the security headers entirely.)
#
# Why this order:
# - CORS outermost: every response that reaches the browser, including a
#   429, a 413 and a 500 from the catch-all, carries
#   Access-Control-Allow-Origin. Otherwise the browser hides the response
#   and the web app cannot tell a rate limit from an outage.
# - expose_headers=["Retry-After"]: Retry-After is not a CORS-safelisted
#   response header, so without this page JS cannot read it, even on a 429
#   that does carry Access-Control-Allow-Origin.
# - _security_headers next, so those responses get the security headers too.
# - The catch-all inside both, so its 500 gets both header sets, and
#   outside the limiter and the size check, so an exception raised by
#   either of them is caught too.
# - _rate_limit before _max_body_size: an oversized request still counts
#   toward the limit.
# tests/route/test_middleware_order.py pins this exact list.
app.add_middleware(BaseHTTPMiddleware, dispatch=_max_body_size)
app.add_middleware(BaseHTTPMiddleware, dispatch=_rate_limit)
app.add_middleware(BaseHTTPMiddleware, dispatch=_catch_unhandled_exceptions)
app.add_middleware(BaseHTTPMiddleware, dispatch=_security_headers)
# The web app is a separate origin in every configuration this project has
# (settings.web_base_url) - without this, every browser request's CORS
# preflight fails before auth (Authorization header) is even considered.
# allow_credentials is not needed - auth is a Bearer header, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base_url],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)


register_exception_handlers(app)
app.include_router(auth.router)
app.include_router(organizations.router)
app.include_router(tenders.router)
app.include_router(documents.router)
app.include_router(workflow.router)
app.include_router(gates.router)
app.include_router(requirements.router)
app.include_router(knowledge.router)
app.include_router(compliance.router)
app.include_router(talent.router)
app.include_router(commercial.router)
app.include_router(proposal.router)
app.include_router(graph.router)
app.include_router(redteam.router)
app.include_router(readiness.router)
app.include_router(operations.router)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


async def _check_postgres() -> bool:
    conn = await asyncpg.connect(dsn=settings.postgres_dsn.replace("+asyncpg", ""))
    try:
        await conn.execute("SELECT 1")
        return True
    finally:
        await conn.close()


async def _check_redis() -> bool:
    client = redis.from_url(settings.redis_url)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


async def _check_neo4j() -> bool:
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )
    try:
        await driver.verify_connectivity()
        return True
    finally:
        await driver.close()


@functools.cache
def _object_storage_probe_client() -> Any:
    # One client for the life of the process, built from the same settings
    # db/storage.py's ObjectStorage uses, so the probe checks exactly the
    # endpoint, credentials and region that uploads will use. One attempt
    # and short timeouts: a readiness probe must answer, not retry.
    return boto3.client(
        "s3",
        endpoint_url=backend_settings.s3_endpoint_url,
        aws_access_key_id=backend_settings.s3_access_key,
        aws_secret_access_key=backend_settings.s3_secret_key,
        region_name=backend_settings.s3_region,
        config=BotoConfig(
            signature_version="s3v4",
            retries={"max_attempts": 1, "mode": "standard"},
            connect_timeout=3,
            read_timeout=5,
        ),
    )


def _head_bucket_ready() -> bool:
    try:
        _object_storage_probe_client().head_bucket(Bucket=backend_settings.s3_bucket)
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status == 404:
            # Reachable and authenticated; the bucket is created lazily on
            # the first upload (ObjectStorage._ensure_bucket), so a fresh
            # stack has none yet. Requiring 200 here would keep a fresh
            # stack unhealthy forever.
            return True
        code = exc.response.get("Error", {}).get("Code")
        logger.warning("object storage not ready: HeadBucket returned HTTP %s (%s)", status, code)
        return False
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        logger.warning("object storage not ready: %s", type(exc).__name__)
        return False
    return True


async def _check_object_storage() -> bool:
    """Vendor-neutral readiness: an authenticated S3 HeadBucket on the app's bucket.

    200 or 404 (bucket not created yet) mean ready; 403 (wrong credentials),
    any other HTTP status, and any connection or client error mean not ready.
    Logs the HTTP status and error code or the exception type, never
    credentials.

    What it cannot see: it checks reachability and credentials, not the
    write path. A server whose front end answers while its storage cannot
    yet accept writes reports ready here; so does a non-S3 server at the
    endpoint that happens to answer HEAD with 404.
    """
    return await asyncio.to_thread(_head_bucket_ready)


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    checks = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "neo4j": _check_neo4j(),
        "object_storage": _check_object_storage(),
    }
    results = await asyncio.gather(*checks.values(), return_exceptions=True)
    status = {name: (result is True) for name, result in zip(checks.keys(), results, strict=True)}
    ready = all(status.values())
    body = {"ready": ready, "dependencies": status}
    return JSONResponse(content=body, status_code=200 if ready else 503)
