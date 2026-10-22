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

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError
from tenderos_api import main as api_main
from tenderos_api import rate_limit

pytestmark = pytest.mark.asyncio

# _cleanup_redis now lives in tests/route/conftest.py (shared with
# test_middleware_order.py).


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


async def test_options_preflight_is_never_rate_limited(client: TestClient, monkeypatch, _cleanup_redis):
    """B16 correction - OPTIONS used to be counted like any other request
    (confirmed live: a real E2E run's own CORS preflight got a real 429
    on a request the bypass header should have covered but reliably
    reaching a browser-generated preflight was never solid ground to
    begin with). Exempt unconditionally now, not just under the E2E
    bypass - checked here with the bypass flag OFF and no bypass header
    at all, since the fix is "preflights don't count," not "preflights
    are covered by the same escape hatch real requests use."
    """
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main.settings, "e2e_rate_limit_bypass_enabled", False)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 2)

    preflight_headers = {
        "Origin": api_main.settings.web_base_url,
        "Access-Control-Request-Method": "GET",
    }
    # Trip the real limiter for real first (patched to 2) - a preflight
    # test that never actually exceeds the limit would prove nothing about
    # whether OPTIONS traffic counts toward it.
    first = client.get("/health/live")
    second = client.get("/health/live")
    third = client.get("/health/live")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text

    # Now, against an ALREADY-tripped counter: every preflight still
    # succeeds - proving OPTIONS traffic never touches the counter at all,
    # not just that it happened to stay under the limit.
    for _ in range(5):
        response = client.options("/health/live", headers=preflight_headers)
        assert response.status_code == 200, response.text

    # And a real request is still correctly blocked - the exemption didn't
    # accidentally loosen protection for genuine traffic.
    still_blocked = client.get("/health/live")
    assert still_blocked.status_code == 429, still_blocked.text


async def test_disabled_by_default_in_this_test_session():
    """Confirms the documented default this whole test session relies on
    is real, not just asserted in a comment - Makefile's own test target
    sets RATE_LIMIT_ENABLED=false, and nothing in this file's own other
    tests should be read as proof the flag defaults to enabled."""
    assert api_main.settings.rate_limit_enabled is False


async def test_e2e_bypass_header_does_nothing_when_the_flag_is_off(
    client: TestClient, monkeypatch, _cleanup_redis
):
    """B16: the requirement this bug fix exists to satisfy - a client
    sending the bypass header, on its own, must not change behavior at
    all. e2e_rate_limit_bypass_enabled is left at its real default
    (False) here deliberately - not monkeypatched to False, which would
    only prove the test's own setup works, not that the shipped default
    is what it claims to be."""
    assert api_main.settings.e2e_rate_limit_bypass_enabled is False
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 2)

    headers = {"X-TenderOS-E2E-Bypass": "playwright"}
    first = client.get("/health/live", headers=headers)
    second = client.get("/health/live", headers=headers)
    third = client.get("/health/live", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text


async def test_e2e_bypass_works_only_when_both_the_flag_and_header_are_present(
    client: TestClient, monkeypatch, _cleanup_redis
):
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main.settings, "e2e_rate_limit_bypass_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 2)

    # Trip the real limiter first, with no header at all - confirms the
    # flag being on does NOT loosen the limit for an ordinary caller who
    # never sends the header. A bypassed request (below) skips
    # check_rate_limit entirely and never touches this counter, so this
    # has to be trod for real, not inferred from the bypass loop's own
    # requests.
    first = client.get("/health/live")
    second = client.get("/health/live")
    third = client.get("/health/live")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert third.status_code == 429, third.text

    # The wrong header value doesn't bypass either - still blocked, same
    # already-tripped counter.
    wrong_value = client.get("/health/live", headers={"X-TenderOS-E2E-Bypass": "not-playwright"})
    assert wrong_value.status_code == 429, wrong_value.text

    # Now the real bypass: correct header, flag on, against a counter
    # that's ALREADY tripped for a normal caller - proves the bypass
    # genuinely ignores the limit rather than merely not having reached it
    # yet.
    headers = {"X-TenderOS-E2E-Bypass": "playwright"}
    for _ in range(5):
        response = client.get("/health/live", headers=headers)
        assert response.status_code == 200, response.text


# Phase 4 blocker 1: the limiter fails open. When Redis cannot be reached
# or times out, the request goes through and a WARNING is logged, for both
# the global and the login limit.


def _limiter_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "tenderos_api.main"
        and record.levelno == logging.WARNING
        and "Redis unavailable" in record.getMessage()
    ]


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("GET", "/health/live", 200),
        # Unknown email: the route's own answer is 401, which is only
        # reachable if the limiter let the request through.
        ("POST", "/auth/login", 401),
    ],
)
async def test_limiter_fails_open_when_redis_is_unreachable(
    client: TestClient,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    method: str,
    path: str,
    expected_status: int,
):
    """A real connection failure, not a raised stand-in: the limiter's own
    client (rate_limit.build_client, so the real timeouts and retry policy)
    pointed at a port nothing listens on. Depending on the OS this surfaces
    as a refused connection or as the 250 ms connect timeout; both are in
    REDIS_UNAVAILABLE_ERRORS, and both must fail open."""
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)

    @asynccontextmanager
    async def _unreachable_redis() -> AsyncIterator[AsyncRedis]:
        redis_client = rate_limit.build_client("redis://127.0.0.1:1/1")
        try:
            yield redis_client
        finally:
            await redis_client.aclose()

    monkeypatch.setattr(api_main, "rate_limit_redis", _unreachable_redis)
    caplog.set_level(logging.WARNING, logger="tenderos_api.main")

    body = {"email": f"{uuid.uuid4()}@example.com", "password": "wrong-password"}
    response = client.request(method, path, json=body if method == "POST" else None)

    assert response.status_code == expected_status, response.text
    warnings = _limiter_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    # Only the exception type is logged, never the exception's own message,
    # which for a connection error names the host and port.
    message = warnings[0].getMessage()
    assert "allowing the request" in message
    assert "127.0.0.1" not in message


async def test_login_limit_fails_open_on_its_own(
    client: TestClient,
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    _cleanup_redis,
):
    """The login check has its own Redis call after the global one. Redis
    is real and healthy for the global check here; only the login-key
    check raises, so this proves the login limit's own failure path fails
    open, not just a failure that happened to come first."""
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    real_check_rate_limit = api_main.check_rate_limit

    async def _fail_on_login_key(redis_client, key, limit, window_seconds):
        if key.startswith("ratelimit:login:"):
            raise RedisConnectionError("simulated outage on the login check")
        return await real_check_rate_limit(redis_client, key, limit, window_seconds)

    monkeypatch.setattr(api_main, "check_rate_limit", _fail_on_login_key)
    caplog.set_level(logging.WARNING, logger="tenderos_api.main")

    response = client.post(
        "/auth/login",
        json={"email": f"{uuid.uuid4()}@example.com", "password": "wrong-password"},
    )

    assert response.status_code == 401, response.text
    assert len(_limiter_warnings(caplog)) == 1
