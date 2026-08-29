"""B15 CodeRabbit finding: main.py::_max_body_size rejects an oversized
request body via its declared Content-Length before Starlette ever parses
it - see that middleware's own comment for why (multipart parsing happens
inside routing, before any route-level bounded-read code such as
documents.py's _read_bounded gets a chance to run at all).
"""

import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

pytestmark = pytest.mark.asyncio

# backend Settings.max_upload_size_mb (50) + 1MB - real bytes actually sent,
# not a manually-set header claiming a size that was never transmitted, so
# this proves the middleware rejects a genuinely oversized body, not just a
# header value that happens to disagree with what follows it.
_OVER_LIMIT_BYTES = 51 * 1024 * 1024


async def test_an_oversized_request_body_is_rejected_before_routing():
    """No specific route needs to exist - _max_body_size runs before
    routing resolves the path at all, so this proves the rejection is a
    real content-length short-circuit, not something routing produced."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/does-not-matter", content=b"x" * _OVER_LIMIT_BYTES)

    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


async def test_a_small_request_is_not_rejected_by_this_middleware():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/health/live")

    assert resp.status_code != 413
