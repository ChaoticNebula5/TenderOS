import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_admin(client: AsyncClient, email: str, org_name: str) -> tuple[str, str]:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = resp.json()
    return data["access_token"], data["organization_id"]


async def test_duplicate_idempotency_key_same_body_replays_response():
    """Depends on both dependencies.require_permission and idempotency.
    get_idempotency_context being implemented - errors with 500 until both
    TODOs are filled in."""
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-a@example.com", "Idem Org A")
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-tender-1"}
        body = {"name": "Idempotent RFP"}

        first = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)
        second = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json() == second.json()

        listing = await client.get(
            f"/organizations/{org_id}/tenders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(listing.json()) == 1


async def test_duplicate_idempotency_key_different_body_conflicts():
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-b@example.com", "Idem Org B")
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-tender-2"}

        first = await client.post(
            f"/organizations/{org_id}/tenders", json={"name": "First Name"}, headers=headers
        )
        second = await client.post(
            f"/organizations/{org_id}/tenders", json={"name": "Different Name"}, headers=headers
        )

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_missing_idempotency_key_creates_separate_tenders():
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-c@example.com", "Idem Org C")
        headers = {"Authorization": f"Bearer {token}"}
        body = {"name": "No Key RFP"}

        first = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)
        second = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]
