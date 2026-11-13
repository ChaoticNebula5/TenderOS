"""Route-level coverage for auth.py (G1) - the harness proof-of-concept
(§7 step 1 of the B13 plan) plus real coverage for this module's routes,
none of which any prior integration test exercised through the actual HTTP
layer (every existing test calls auth_service directly).

Plan item 34, option 2: the refresh token is only ever in the HttpOnly
cookie. The test client's cookie jar respects the cookie's Path (/api/auth,
the browser's path; the api itself sees /auth) and its Secure flag
(http://testserver is not secure), so it never sends the cookie on its own:
each test sends the Cookie header explicitly, as the browser would.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from http.cookies import Morsel, SimpleCookie

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient, Response
from tenderos_api.config import settings
from tenderos_api.main import app
from tenderos_backend.core.security import decode_token
from tenderos_backend.db.repositories.refresh_tokens import RefreshTokenRepository

COOKIE = "tenderos_refresh"
WEB = {"X-TenderOS-Client": "web"}
_PASSWORD = "correct horse battery staple 1"
_WAIT_SECONDS = 10


def _refresh_cookie(response: Response) -> Morsel:
    """The one Set-Cookie for the refresh cookie in this response."""
    found = []
    for header in response.headers.get_list("set-cookie"):
        parsed: SimpleCookie = SimpleCookie()
        parsed.load(header)
        if COOKIE in parsed:
            found.append(parsed[COOKIE])
    assert len(found) == 1, response.headers.get_list("set-cookie")
    return found[0]


def _sends(token: str) -> dict[str, str]:
    return {"Cookie": f"{COOKIE}={token}"}


def _assert_session_cookie(response: Response) -> str:
    """The cookie's attributes, and its Max-Age against the token's own exp
    (the family's expiry). Returns the token."""
    morsel = _refresh_cookie(response)
    assert morsel["httponly"] is True
    assert morsel["secure"] is True
    assert morsel["samesite"].lower() == "strict"
    assert morsel["path"] == "/api/auth"
    assert morsel["domain"] == ""
    exp = decode_token(morsel.value)["exp"]
    assert isinstance(exp, int)
    remaining = exp - int(datetime.now(UTC).timestamp())
    assert abs(int(morsel["max-age"]) - remaining) <= 5, (morsel["max-age"], remaining)
    return morsel.value


def _assert_cookie_cleared(response: Response) -> None:
    morsel = _refresh_cookie(response)
    assert morsel["max-age"] == "0"
    assert morsel["path"] == "/api/auth"
    assert morsel.value in ("", '""')


def _register(client: TestClient, org_name: str = "Session Route Test Org") -> Response:
    response = client.post(
        "/auth/register",
        json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": _PASSWORD,
            "organization_name": org_name,
        },
    )
    assert response.status_code == 201, response.text
    return response


def test_register_creates_account_and_sets_the_refresh_cookie(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": _PASSWORD,
            "organization_name": "Auth Route Test Org",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == email
    assert body["user"]["is_active"] is True
    assert body["access_token"]
    assert body["organization_id"]
    assert "refresh_token" not in body
    token = _assert_session_cookie(response)
    assert decode_token(token)["token_type"] == "refresh"
    # Roughly the family's 7 days.
    assert int(_refresh_cookie(response)["max-age"]) > 6 * 24 * 3600


def test_register_rejects_duplicate_email(client: TestClient, register_actor):
    _, _, _ = register_actor(email="duplicate@example.com")
    response = client.post(
        "/auth/register",
        json={
            "email": "duplicate@example.com",
            "password": "another password entirely",
            "organization_name": "Second Org",
        },
    )
    assert response.status_code != 201
    assert response.status_code == 409, response.text


def test_login_returns_an_access_token_and_sets_the_refresh_cookie(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    client.post(
        "/auth/register",
        json={"email": email, "password": _PASSWORD, "organization_name": "Login Test Org"},
    )

    response = client.post("/auth/login", json={"email": email, "password": _PASSWORD})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert "refresh_token" not in body
    _assert_session_cookie(response)


def test_login_rejects_wrong_password(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "the real password",
            "organization_name": "Wrong Password Test Org",
        },
    )

    response = client.post("/auth/login", json={"email": email, "password": "not the real one"})

    assert response.status_code == 401, response.text
    assert response.headers.get_list("set-cookie") == []


def test_login_rejects_unknown_email(client: TestClient):
    response = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert response.status_code == 401, response.text


def test_refresh_reads_the_cookie_and_sets_the_rotated_one(client: TestClient):
    first = _refresh_cookie(_register(client)).value

    response = client.post("/auth/refresh", headers=_sends(first))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert "refresh_token" not in body
    # Plan item 34, option 3: every refresh rotates the refresh token.
    second = _assert_session_cookie(response)
    assert second != first
    assert decode_token(second)["exp"] == decode_token(first)["exp"]


@pytest.mark.asyncio
async def test_the_cookies_max_age_follows_the_familys_expiry_not_a_fresh_7_days():
    """Rotation keeps the family's expires_at, so a rotated cookie's Max-Age
    is what is left of the family, however recent the rotation. The family
    is aged here by moving its expires_at to an hour from now."""
    from sqlalchemy import func, update
    from tenderos_backend.db.models.refresh_token import RefreshToken
    from tenderos_backend.db.unit_of_work import UnitOfWork

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=WEB
    ) as tab:
        registered = await tab.post(
            "/auth/register",
            json={
                "email": f"{uuid.uuid4()}@example.com",
                "password": _PASSWORD,
                "organization_name": "Max-Age Org",
            },
        )
        first = _refresh_cookie(registered).value
        token_id = uuid.UUID(str(decode_token(first)["jti"]))
        async with UnitOfWork() as uow:
            await uow.session.execute(
                update(RefreshToken)
                .where(RefreshToken.id == token_id)
                .values(expires_at=func.now() + timedelta(hours=1))
            )
            await uow.commit()

        rotated = await tab.post("/auth/refresh", headers=_sends(first))

    assert rotated.status_code == 200, rotated.text
    max_age = int(_refresh_cookie(rotated)["max-age"])
    assert 3600 - 10 <= max_age <= 3600, max_age
    _assert_session_cookie(rotated)


def test_refresh_ignores_a_token_in_the_body(client: TestClient):
    """The body is not read at all: a script holding a token cannot refresh
    with it unless it can also set the cookie."""
    first = _refresh_cookie(_register(client)).value

    response = client.post("/auth/refresh", json={"refresh_token": first})

    assert response.status_code == 401, response.text
    _assert_cookie_cleared(response)


@pytest.mark.parametrize("cookie", [None, "garbage"], ids=["missing", "invalid"])
def test_a_refresh_without_a_valid_cookie_is_401_and_clears_it(
    client: TestClient, cookie: str | None
):
    headers = {} if cookie is None else _sends(cookie)

    response = client.post("/auth/refresh", headers=headers)

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "INVALID_TOKEN"
    _assert_cookie_cleared(response)


def test_refresh_rejects_an_access_token_sent_as_the_cookie(client: TestClient):
    access_token = _register(client).json()["access_token"]

    response = client.post("/auth/refresh", headers=_sends(access_token))

    assert response.status_code == 401, response.text


def test_me_returns_the_authenticated_user_and_their_membership(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()

    response = client.get("/auth/me", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["id"] == user_id
    assert len(body["memberships"]) == 1
    assert body["memberships"][0]["organization_id"] == org_id
    assert body["memberships"][0]["role"] == "admin"


def test_me_rejects_an_unauthenticated_request(client: TestClient):
    response = client.get("/auth/me")
    assert response.status_code in (401, 403), response.text


def test_a_reused_refresh_token_is_refused_and_ends_the_family(client: TestClient, monkeypatch):
    """Through HTTP: after the grace window a spent token gets 401, and so
    does the newest token of its family, because reuse revoked it."""
    from datetime import timedelta

    from tenderos_backend.domain.services import auth_service

    monkeypatch.setattr(auth_service, "REFRESH_REUSE_GRACE", timedelta(0))
    first = _refresh_cookie(_register(client)).value
    second = _refresh_cookie(client.post("/auth/refresh", headers=_sends(first))).value

    reused = client.post("/auth/refresh", headers=_sends(first))
    after = client.post("/auth/refresh", headers=_sends(second))

    assert reused.status_code == 401, reused.text
    assert reused.json()["error"]["code"] == "INVALID_TOKEN"
    assert after.status_code == 401, after.text


def test_logout_revokes_the_family_and_clears_the_cookie(client: TestClient):
    token = _refresh_cookie(_register(client)).value

    response = client.post("/auth/logout", headers=_sends(token))

    assert response.status_code == 204, response.text
    assert response.content == b""
    _assert_cookie_cleared(response)
    refused = client.post("/auth/refresh", headers=_sends(token))
    assert refused.status_code == 401, refused.text


def test_logout_needs_no_access_token_and_answers_204_for_any_cookie(client: TestClient):
    registered = _register(client)
    token = _refresh_cookie(registered).value

    for headers in (
        {},
        _sends("garbage"),
        _sends(registered.json()["access_token"]),
        _sends(token),
    ):
        response = client.post("/auth/logout", headers=headers)
        assert response.status_code == 204, (list(headers), response.text)
        _assert_cookie_cleared(response)
    # Twice for the same token: already revoked, still 204.
    again = client.post("/auth/logout", headers=_sends(token))
    assert again.status_code == 204, again.text


def test_an_access_token_issued_before_logout_still_works_until_it_expires(client: TestClient):
    """The accepted limit (KNOWN_LIMITATIONS, the session entry): logout
    revokes refresh tokens, not access tokens already issued."""
    registered = _register(client)
    client.post("/auth/logout", headers=_sends(_refresh_cookie(registered).value))

    access = registered.json()["access_token"]
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {access}"})

    assert response.status_code == 200, response.text


def test_a_refresh_token_without_a_jti_is_refused(client: TestClient):
    """A token shaped like every refresh token issued before migration 021."""
    from datetime import timedelta

    from tenderos_backend.core.security import _create_token

    registered = _register(client)
    legacy = _create_token(uuid.UUID(registered.json()["user"]["id"]), "refresh", timedelta(days=7))

    response = client.post("/auth/refresh", headers=_sends(legacy))

    assert response.status_code == 401, response.text


# --- CSRF: the client header and the Origin ---------------------------------

_LOGIN_BODY = {"email": "nobody@example.com", "password": "whatever"}
_REGISTER_BODY = {
    "email": "csrf-nobody@example.com",
    "password": "whatever",
    "organization_name": "CSRF Org",
}
_COOKIE_ROUTES = [
    ("/auth/login", _LOGIN_BODY),
    ("/auth/register", _REGISTER_BODY),
    ("/auth/refresh", None),
    ("/auth/logout", None),
]
_ROUTE_IDS = [path for path, _ in _COOKIE_ROUTES]


@pytest.mark.parametrize(("path", "body"), _COOKIE_ROUTES, ids=_ROUTE_IDS)
def test_the_cookie_routes_refuse_a_request_without_the_client_header(path: str, body):
    bare = TestClient(app)

    response = bare.post(path, json=body)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_CHECK_FAILED"
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.parametrize(("path", "body"), _COOKIE_ROUTES, ids=_ROUTE_IDS)
def test_the_cookie_routes_refuse_the_wrong_header_value(path: str, body):
    bare = TestClient(app)

    response = bare.post(path, json=body, headers={"X-TenderOS-Client": "curl"})

    assert response.status_code == 403, response.text


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://localhost:5174"])
@pytest.mark.parametrize(("path", "body"), _COOKIE_ROUTES, ids=_ROUTE_IDS)
def test_the_cookie_routes_refuse_a_foreign_origin_even_with_the_header(
    client: TestClient, path: str, body, origin: str
):
    response = client.post(path, json=body, headers={"Origin": origin})

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "CSRF_CHECK_FAILED"


def test_the_web_apps_own_origin_with_the_header_is_let_through(client: TestClient):
    registered = _register(client)
    token = _refresh_cookie(registered).value

    response = client.post(
        "/auth/refresh", headers={**_sends(token), "Origin": settings.web_base_url}
    )

    assert response.status_code == 200, response.text


def test_the_check_is_only_on_the_cookie_routes(client: TestClient, register_actor):
    """/auth/me and the rest take the bearer token and need no header."""
    headers, _, _ = register_actor()

    response = TestClient(app).get("/auth/me", headers=headers)

    assert response.status_code == 200, response.text


# --- two tabs refreshing at once, without Web Locks (option 2's condition) ---


def _hold_at_the_family_lock(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every refresh waits at a barrier just before it takes the family lock,
    so the requests genuinely overlap instead of running one after another by
    luck (the same barrier as tests/integration/test_refresh_tokens.py)."""
    barrier = asyncio.Barrier(parties)
    original = RefreshTokenRepository.lock_family

    async def lock_together(self, family_id):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        return await original(self, family_id)

    monkeypatch.setattr(RefreshTokenRepository, "lock_family", lock_together)


@pytest.mark.asyncio
async def test_two_tabs_refreshing_at_once_both_get_the_same_live_cookie(monkeypatch):
    """Two tabs of one browser send the same cookie at the same moment, with
    no Web Lock to serialise them. The browser keeps whichever Set-Cookie it
    processes last, so both answers must set the same token, and it must be
    live: the next refresh with it succeeds. Repeated, and the follow-up uses
    the first answer's cookie on even rounds and the second's on odd ones,
    so both processing orders are covered."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=WEB
    ) as tabs:
        for round_number in range(6):
            registered = await tabs.post(
                "/auth/register",
                json={
                    "email": f"{uuid.uuid4()}@example.com",
                    "password": _PASSWORD,
                    "organization_name": "Two Tabs Org",
                },
            )
            first = _refresh_cookie(registered).value
            _hold_at_the_family_lock(monkeypatch, 2)

            answers = await asyncio.wait_for(
                asyncio.gather(
                    tabs.post("/auth/refresh", headers=_sends(first)),
                    tabs.post("/auth/refresh", headers=_sends(first)),
                ),
                timeout=_WAIT_SECONDS,
            )
            monkeypatch.undo()

            assert [a.status_code for a in answers] == [200, 200], [a.text for a in answers]
            cookies = [_refresh_cookie(a).value for a in answers]
            assert cookies[0] == cookies[1]
            assert cookies[0] != first
            # Each tab still gets its own access token.
            assert all(a.json()["access_token"] for a in answers)

            kept = cookies[round_number % 2]
            follow_up = await tabs.post("/auth/refresh", headers=_sends(kept))
            assert follow_up.status_code == 200, follow_up.text
            # Still a normal chain after it: nothing was revoked.
            after = await tabs.post(
                "/auth/refresh", headers=_sends(_refresh_cookie(follow_up).value)
            )
            assert after.status_code == 200, after.text
