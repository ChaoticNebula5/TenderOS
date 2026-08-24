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
    assert response.json()["access_token"]


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


def test_me_returns_the_authenticated_user_and_their_membership(
    client: TestClient, register_actor
):
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
