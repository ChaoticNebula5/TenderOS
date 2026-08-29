"""B15: real, end-to-end proof the rate-limit middleware itself returns 429
when tripped - over real HTTP, against the real app. Every test here
deliberately re-enables settings.rate_limit_enabled (off by default for
this whole test session - see tenderos_api/config.py's own comment) and
patches the limit constants down so a handful of real requests trip it,
rather than needing 100+ real calls. tests/unit/test_rate_limit.py covers
the counting logic itself in isolation.

Each test restores whatever it patched via monkeypatch's own teardown and
clears the Redis keys it created, so this file cannot leak rate-limit
state into any other test in the shared session (every other test runs
with rate_limit_enabled=False and would never read these keys anyway, but
cleaning up is still the right default).
"""

import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from redis.asyncio import Redis as AsyncRedis
from tenderos_api import main as api_main

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def _cleanup_redis():
    yield
    client = AsyncRedis.from_url(api_main.settings.redis_url)
    async for key in client.scan_iter(match="ratelimit:*"):
        await client.delete(key)
    await client.aclose()


async def test_global_limit_returns_429_with_retry_after(
    client: TestClient, monkeypatch, _cleanup_redis
):
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 2)

    first = client.get("/health/live")
    second = client.get("/health/live")
    third = client.get("/health/live")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text
    assert third.json()["error"]["code"] == "RATE_LIMITED"
    assert int(third.headers["Retry-After"]) > 0


async def test_login_has_its_own_tighter_limit(client: TestClient, monkeypatch, _cleanup_redis):
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "LOGIN_LIMIT", 2)

    email = f"{uuid.uuid4()}@example.com"
    client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": "Rate Limit Org"},
    )

    body = {"email": email, "password": "wrong-password"}
    first = client.post("/auth/login", json=body)
    second = client.post("/auth/login", json=body)
    third = client.post("/auth/login", json=body)

    # Real credentials wouldn't change this - the limit counts attempts,
    # not failures, which is the whole point against credential stuffing.
    assert first.status_code == 401, first.text
    assert second.status_code == 401, second.text
    assert third.status_code == 429, third.text
    assert third.json()["error"]["code"] == "RATE_LIMITED"


async def test_disabled_by_default_in_this_test_session():
    """Confirms the documented default this whole test session relies on
    is real, not just asserted in a comment - Makefile's own test target
    sets RATE_LIMIT_ENABLED=false, and nothing in this file's own other
    tests should be read as proof the flag defaults to enabled."""
    assert api_main.settings.rate_limit_enabled is False
