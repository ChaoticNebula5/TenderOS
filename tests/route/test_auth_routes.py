"""Route-level coverage for auth.py (G1) - the harness proof-of-concept
(§7 step 1 of the B13 plan) plus real coverage for this module's 4 routes,
none of which any prior integration test exercised through the actual HTTP
layer (every existing test calls auth_service directly)."""

import uuid

from fastapi.testclient import TestClient


def test_register_creates_account_and_returns_tokens(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple 1",
            "organization_name": "Auth Route Test Org",
            "display_name": "Test User",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == email
    assert body["user"]["is_active"] is True
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["organization_id"]


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


def test_login_returns_tokens_for_correct_credentials(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple 1",
            "organization_name": "Login Test Org",
        },
    )

    response = client.post(
        "/auth/login", json={"email": email, "password": "correct horse battery staple 1"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


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


def test_login_rejects_unknown_email(client: TestClient):
    response = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever"}
    )
    assert response.status_code == 401, response.text


def test_refresh_returns_a_new_access_token(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    register = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple 1",
            "organization_name": "Refresh Test Org",
        },
    )
    refresh_token = register.json()["refresh_token"]

    response = client.post("/auth/refresh", json={"refresh_token": refresh_token})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    # Plan item 34, option 3: every refresh rotates the refresh token.
    assert body["refresh_token"]
    assert body["refresh_token"] != refresh_token


def test_refresh_rejects_an_access_token_used_as_a_refresh_token(client: TestClient):
    email = f"{uuid.uuid4()}@example.com"
    register = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple 1",
            "organization_name": "Refresh Reject Test Org",
        },
    )
    access_token = register.json()["access_token"]

    response = client.post("/auth/refresh", json={"refresh_token": access_token})

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


def _register_tokens(client: TestClient) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": f"{uuid.uuid4()}@example.com",
            "password": "correct horse battery staple 1",
            "organization_name": "Session Route Test Org",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_a_reused_refresh_token_is_refused_and_ends_the_family(client: TestClient, monkeypatch):
    """Through HTTP: after the grace window a spent token gets 401, and so
    does the newest token of its family, because reuse revoked it."""
    from datetime import timedelta

    from tenderos_backend.domain.services import auth_service

    monkeypatch.setattr(auth_service, "REFRESH_REUSE_GRACE", timedelta(0))
    first = _register_tokens(client)["refresh_token"]
    second = client.post("/auth/refresh", json={"refresh_token": first}).json()["refresh_token"]

    reused = client.post("/auth/refresh", json={"refresh_token": first})
    after = client.post("/auth/refresh", json={"refresh_token": second})

    assert reused.status_code == 401, reused.text
    assert reused.json()["error"]["code"] == "INVALID_TOKEN"
    assert after.status_code == 401, after.text


def test_logout_returns_204_and_the_refresh_token_then_gets_401(client: TestClient):
    tokens = _register_tokens(client)

    response = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 204, response.text
    assert response.content == b""
    refused = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refused.status_code == 401, refused.text


def test_logout_needs_no_access_token_and_answers_204_for_any_token(client: TestClient):
    tokens = _register_tokens(client)

    for token in ("garbage", tokens["access_token"], tokens["refresh_token"]):
        response = client.post("/auth/logout", json={"refresh_token": token})
        assert response.status_code == 204, (token[:8], response.text)
    # Twice for the same token: already revoked, still 204.
    again = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    assert again.status_code == 204, again.text


def test_an_access_token_issued_before_logout_still_works_until_it_expires(client: TestClient):
    """The accepted limit (KNOWN_LIMITATIONS, the session entry): logout
    revokes refresh tokens, not access tokens already issued."""
    tokens = _register_tokens(client)
    client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})

    assert response.status_code == 200, response.text


def test_a_refresh_token_without_a_jti_is_refused(client: TestClient):
    """A token shaped like every refresh token issued before migration 021."""
    from datetime import timedelta

    from tenderos_backend.core.security import _create_token

    tokens = _register_tokens(client)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    legacy = _create_token(uuid.UUID(me.json()["user"]["id"]), "refresh", timedelta(days=7))

    response = client.post("/auth/refresh", json={"refresh_token": legacy})

    assert response.status_code == 401, response.text
