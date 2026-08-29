import asyncio
from collections.abc import Awaitable, Callable

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase
from tenderos_backend.core.log_redaction import install_log_redaction

from tenderos_api.config import settings
from tenderos_api.errors import register_exception_handlers
from tenderos_api.rate_limit import (
    GLOBAL_LIMIT,
    GLOBAL_WINDOW_SECONDS,
    LOGIN_LIMIT,
    LOGIN_WINDOW_SECONDS,
    check_rate_limit,
    client_key,
)
from tenderos_api.routes import (
    auth,
    commercial,
    compliance,
    documents,
    gates,
    graph,
    knowledge,
    organizations,
    proposal,
    readiness,
    redteam,
    requirements,
    talent,
    tenders,
    workflow,
)

# B15: real, process-wide (this module loads once per uvicorn process),
# so this covers every route/service call this process ever makes - see
# log_redaction.py's own module docstring for what this does and doesn't
# cover.
install_log_redaction()

app = FastAPI(title="TenderOS API")
# The web app is a separate origin in every configuration this project has
# (settings.web_base_url) - without this, every browser request's CORS
# preflight fails before auth (Authorization header) is even considered.
# allow_credentials is not needed - auth is a Bearer header, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_base_url],
    allow_methods=["*"],
    allow_headers=["*"],
)

# B15: secure headers - nothing set these before. This is a pure JSON API
# (no HTML views of its own) except for FastAPI's auto-generated /docs and
# /redoc, which need to load their own JS/CSS - a blanket
# `default-src 'none'` CSP would break Swagger UI, so those two paths (plus
# the /openapi.json they both fetch) are exempt from CSP specifically, not
# from the other headers below, which are safe everywhere regardless of
# content type.
_CSP_EXEMPT_PATHS = {"/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
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


# B15: registered after _security_headers (Starlette wraps middleware in
# the reverse of add order - the first one added is outermost), so an
# early 429 returned here without ever calling call_next still passes
# back out through _security_headers and picks up the same header set
# every other response gets, rather than skipping it.
@app.middleware("http")
async def _rate_limit(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if not settings.rate_limit_enabled:
        return await call_next(request)

    redis_client = redis.from_url(settings.redis_url)
    ip = client_key(request.client.host if request.client else None)

    allowed, retry_after = await check_rate_limit(
        redis_client, f"ratelimit:global:{ip}", GLOBAL_LIMIT, GLOBAL_WINDOW_SECONDS
    )
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": {"code": "RATE_LIMITED", "message": "Too many requests."}},
            headers={"Retry-After": str(retry_after)},
        )

    # A tighter, dedicated limit on the one route a credential-stuffing/
    # brute-force attempt would actually hit - checked in addition to,
    # not instead of, the global limit above.
    if request.url.path == "/auth/login":
        allowed, retry_after = await check_rate_limit(
            redis_client, f"ratelimit:login:{ip}", LOGIN_LIMIT, LOGIN_WINDOW_SECONDS
        )
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {"code": "RATE_LIMITED", "message": "Too many login attempts."}
                },
                headers={"Retry-After": str(retry_after)},
            )

    return await call_next(request)


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


async def _check_minio() -> bool:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{settings.s3_endpoint_url}/minio/health/live", timeout=5.0)
        return response.status_code == 200


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
    checks = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "neo4j": _check_neo4j(),
        "minio": _check_minio(),
    }
    results = await asyncio.gather(*checks.values(), return_exceptions=True)
    status = {name: (result is True) for name, result in zip(checks.keys(), results, strict=True)}
    ready = all(status.values())
    body = {"ready": ready, "dependencies": status}
    return JSONResponse(content=body, status_code=200 if ready else 503)
