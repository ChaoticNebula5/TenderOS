"""Phase 4 blocker 1: every response the browser can receive carries CORS
headers, including the ones produced by middleware (the 429 and the 413)
and an unhandled exception (the 500).

Before this fix the rate limiter and the body-size check sat OUTSIDE
CORSMiddleware, and an unhandled exception reached Starlette's own
ServerErrorMiddleware, which is outside everything, so those responses had
no Access-Control-Allow-Origin. A browser hides such a response from page
JS, and the web app showed "Could not reach the server" for a rate limit.

These tests check the server half of CORS: the headers on the response. A
browser is still what enforces CORS; the live curl check and the E2E run
cover that half.
"""

import logging
import uuid

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from tenderos_api import main as api_main
from tenderos_api.main import app
from tenderos_backend.domain.services import auth_service

pytestmark = pytest.mark.asyncio

_ORIGIN = api_main.settings.web_base_url

# backend Settings.max_upload_size_mb (50) + 1 MB, real bytes sent, the same
# reasoning as test_max_body_size.py.
_OVER_LIMIT_BYTES = 51 * 1024 * 1024


def _assert_cors(response) -> None:
    assert response.headers.get("access-control-allow-origin") == _ORIGIN, dict(response.headers)


def _assert_security_headers(response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["content-security-policy"] == "default-src 'none'"


async def test_middleware_order_is_pinned():
    """The whole user-middleware list, outermost first, not just index 0:
    any swap in this list changes which responses get CORS or security
    headers, and which exceptions the catch-all sees. Starlette's own
    ServerErrorMiddleware and ExceptionMiddleware are added around this
    list when the stack is built, so they never appear in it."""
    layers = [m.kwargs.get("dispatch", m.cls) for m in app.user_middleware]

    assert layers == [
        CORSMiddleware,
        api_main._security_headers,
        api_main._catch_unhandled_exceptions,
        api_main._rate_limit,
        api_main._max_body_size,
    ]


async def test_cors_exposes_retry_after():
    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["expose_headers"] == ["Retry-After"]


async def test_429_carries_cors_and_exposes_retry_after(
    client: TestClient, monkeypatch, _cleanup_redis
):
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 1)

    first = client.get("/health/live", headers={"Origin": _ORIGIN})
    limited = client.get("/health/live", headers={"Origin": _ORIGIN})

    assert first.status_code == 200, first.text
    assert limited.status_code == 429, limited.text
    assert limited.json() == {"error": {"code": "RATE_LIMITED", "message": "Too many requests."}}
    assert int(limited.headers["retry-after"]) > 0
    _assert_cors(limited)
    exposed = [
        h.strip().lower() for h in limited.headers["access-control-expose-headers"].split(",")
    ]
    assert "retry-after" in exposed


async def test_429_carries_security_headers(client: TestClient, monkeypatch, _cleanup_redis):
    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "GLOBAL_LIMIT", 1)

    client.get("/health/live")
    limited = client.get("/health/live")

    assert limited.status_code == 429, limited.text
    _assert_security_headers(limited)


async def test_413_carries_cors(client: TestClient):
    response = client.post(
        "/does-not-matter", content=b"x" * _OVER_LIMIT_BYTES, headers={"Origin": _ORIGIN}
    )

    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    _assert_cors(response)
    _assert_security_headers(response)


_INTERNAL_DETAIL = "internal detail that must never reach a client"


def _route_raises(monkeypatch) -> None:
    async def _boom(**_kwargs):
        raise RuntimeError(_INTERNAL_DETAIL)

    monkeypatch.setattr(auth_service, "login", _boom)


def _limiter_raises(monkeypatch) -> None:
    # A non-Redis error from inside the limiter: a bug, not an outage, so
    # it must NOT be swallowed by the fail-open path. It reaches the
    # catch-all instead.
    async def _boom(*_args, **_kwargs):
        raise RuntimeError(_INTERNAL_DETAIL)

    monkeypatch.setattr(api_main.settings, "rate_limit_enabled", True)
    monkeypatch.setattr(api_main, "check_rate_limit", _boom)


@pytest.mark.parametrize("make_it_raise", [_route_raises, _limiter_raises])
async def test_unhandled_exception_is_a_500_envelope_with_cors(
    monkeypatch, caplog: pytest.LogCaptureFixture, make_it_raise
):
    """raise_server_exceptions=False so that, before the fix, this fails on
    the assertions (a plain-text 500 with no CORS) rather than on TestClient
    re-raising the exception, which would say less about what was wrong."""
    make_it_raise(monkeypatch)
    caplog.set_level(logging.ERROR, logger="tenderos_api.main")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/auth/login",
        json={"email": f"{uuid.uuid4()}@example.com", "password": uuid.uuid4().hex},
        headers={"Origin": _ORIGIN, "X-TenderOS-Client": "web"},
    )

    assert response.status_code == 500, response.text
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Something went wrong on our side. Please try again.",
        }
    }
    assert _INTERNAL_DETAIL not in response.text
    _assert_cors(response)
    _assert_security_headers(response)

    # The real exception is in the log, with its traceback.
    logged = [r for r in caplog.records if r.name == "tenderos_api.main" and r.exc_info]
    assert len(logged) == 1, [r.getMessage() for r in caplog.records]
    assert logged[0].levelno == logging.ERROR
    assert logged[0].getMessage() == "unhandled error on POST /auth/login"
    assert _INTERNAL_DETAIL in str(logged[0].exc_info[1])
