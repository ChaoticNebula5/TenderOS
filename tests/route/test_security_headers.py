"""B15: secure-headers middleware (main.py::_security_headers) applies to
every response, with a CSP exemption for FastAPI's own /docs, /redoc, and
/openapi.json."""

import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_ordinary_route_carries_the_full_header_set():
    async with await _client() as client:
        resp = await client.get("/health/live")

    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "max-age=" in resp.headers["strict-transport-security"]
    assert resp.headers["content-security-policy"] == "default-src 'none'"


async def test_docs_route_is_exempt_from_csp_but_keeps_the_other_headers():
    async with await _client() as client:
        resp = await client.get("/docs")

    assert resp.status_code == 200
    assert "content-security-policy" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"


async def test_error_response_still_carries_the_header_set():
    """Headers must apply regardless of how the response was produced - a
    404 from the router, not just a normal 200 handler return."""
    async with await _client() as client:
        resp = await client.get("/tenders/00000000-0000-0000-0000-000000000000")

    assert resp.status_code in (401, 403, 404)
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"] == "default-src 'none'"
