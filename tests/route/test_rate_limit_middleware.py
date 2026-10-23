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


# Phase 4 blocker 1: the limiter fails open when Redis cannot be reached or
# times out. Phase 4 plan item 2 (decision 0041): except POST /auth/login,
# which fails closed with a 503 and Retry-After. The outage is logged once
# per interval, not once per request.


def _limiter_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == "tenderos_api.main"
        and record.levelno == logging.WARNING
        and "Redis unavailable" in record.getMessage()
    ]


@pytest.fixture(autouse=True)
def _fresh_outage_log(monkeypatch):
    """The outage log is per process, so a line logged by one test would
    suppress the next test's line. Each test gets its own."""
    monkeypatch.setattr(
        api_main,
        "redis_outage_log",
        rate_limit.OutageLog(rate_limit.REDIS_OUTAGE_LOG_INTERVAL_SECONDS),
    )


@pytest.fixture
def unreachable_redis(monkeypatch):
    """A real connection failure, not a raised stand-in: the limiter's own
    client (rate_limit.build_client, so the real timeouts and retry policy)
    pointed at a port nothing listens on. Depending on the OS this surfaces
    as a refused connection or as the 250 ms connect timeout; both are in
    REDIS_UNAVAILABLE_ERRORS."""
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)

    @asynccontextmanager
    async def _unreachable_redis() -> AsyncIterator[AsyncRedis]:
        redis_client = rate_limit.build_client("redis://127.0.0.1:1/1")
        try:
            yield redis_client
        finally:
            await redis_client.aclose()

    monkeypatch.setattr(api_main, "rate_limit_redis", _unreachable_redis)


def _login_body() -> dict[str, str]:
    # Unknown email: the route's own answer would be 401, so any other
    # status comes from the middleware.
    return {"email": f"{uuid.uuid4()}@example.com", "password": "wrong-password"}


async def test_login_fails_closed_when_redis_is_unreachable(
    client: TestClient, unreachable_redis, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.WARNING, logger="tenderos_api.main")
    origin = api_main.settings.web_base_url

    response = client.post("/auth/login", json=_login_body(), headers={"Origin": origin})

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "LOGIN_UNAVAILABLE"
    assert response.headers["Retry-After"] == str(rate_limit.LOGIN_UNAVAILABLE_RETRY_AFTER_SECONDS)
    # Inside CORS and the security headers, like the 429: the browser shows
    # the page this response, and page JS can read Retry-After.
    assert response.headers["access-control-allow-origin"] == origin
    assert "retry-after" in response.headers["access-control-expose-headers"].lower()
    assert response.headers["x-content-type-options"] == "nosniff"
    warnings = _limiter_warnings(caplog)
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    # Only the exception type is logged, never the exception's own message,
    # which for a connection error names the host and port.
    message = warnings[0].getMessage()
    assert "login refused" in message
    assert "127.0.0.1" not in message


async def test_other_routes_still_fail_open_when_redis_is_unreachable(
    client: TestClient, unreachable_redis
):
    response = client.get("/health/live")
    assert response.status_code == 200, response.text


async def test_signed_in_requests_still_work_during_the_outage(
    client: TestClient, register_actor, unreachable_redis
):
    """Decision 0041's reason login alone can fail closed: an access token
    is a JWT verified without Redis, so a route that needs one still
    answers during the outage, for the life of that token
    (jwt_access_minutes). (Registering is not login, so it fails open
    too.)"""
    headers, user_id, _ = register_actor()

    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["user"]["id"] == user_id


async def test_refresh_still_works_during_the_outage(client: TestClient, unreachable_redis):
    """POST /auth/refresh needs no Redis (auth_service.refresh_access_token
    decodes the refresh JWT and reads the user from Postgres; there is no
    refresh-token store) and is not the login path, so it fails open: a
    client that refreshes keeps its session through the outage. The web app
    does not call it (KNOWN_LIMITATIONS, blocker 1, limit 1)."""
    registered = client.post(
        "/auth/register",
        json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": str(uuid.uuid4()),
            "organization_name": "Refresh During Outage Org",
        },
    )
    assert registered.status_code == 201, registered.text

    response = client.post(
        "/auth/refresh", json={"refresh_token": registered.json()["refresh_token"]}
    )

    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


async def test_the_outage_is_logged_once_per_interval_not_per_request(
    client: TestClient, unreachable_redis, monkeypatch, caplog: pytest.LogCaptureFixture
):
    """Ten requests inside one interval, logins and other routes mixed,
    give one WARNING. A clock moved past the interval gives the second line,
    which counts the nine requests the first line did not cover plus its
    own."""
    clock = {"now": 1000.0}
    outage_log = rate_limit.OutageLog(
        rate_limit.REDIS_OUTAGE_LOG_INTERVAL_SECONDS, clock=lambda: clock["now"]
    )
    monkeypatch.setattr(api_main, "redis_outage_log", outage_log)
    caplog.set_level(logging.WARNING, logger="tenderos_api.main")

    statuses = []
    for i in range(10):
        if i % 2 == 0:
            statuses.append(client.post("/auth/login", json=_login_body()).status_code)
        else:
            statuses.append(client.get("/health/live").status_code)

    assert statuses == [503, 200] * 5
    assert len(_limiter_warnings(caplog)) == 1

    clock["now"] += rate_limit.REDIS_OUTAGE_LOG_INTERVAL_SECONDS
    client.get("/health/live")

    warnings = _limiter_warnings(caplog)
    assert len(warnings) == 2
    assert "10 request(s)" in warnings[1].getMessage()


async def test_login_fails_closed_when_only_its_own_check_fails(
    client: TestClient,
    monkeypatch,
    _cleanup_redis,
):
    """The login check has its own Redis call after the global one. Redis
    is real and healthy for the global check here; only the login-key
    check raises, so this proves the login limit's own failure path fails
    closed, not just a failure that happened to come first."""
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    real_check_rate_limit = api_main.check_rate_limit

    async def _fail_on_login_key(redis_client, key, limit, window_seconds):
        if key.startswith("ratelimit:login:"):
            raise RedisConnectionError("simulated outage on the login check")
        return await real_check_rate_limit(redis_client, key, limit, window_seconds)

    monkeypatch.setattr(api_main, "check_rate_limit", _fail_on_login_key)

    response = client.post("/auth/login", json=_login_body())

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "LOGIN_UNAVAILABLE"
