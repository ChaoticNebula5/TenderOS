import asyncio

import asyncpg
import httpx
import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase

from tenderos_api.config import settings
from tenderos_api.errors import register_exception_handlers
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
