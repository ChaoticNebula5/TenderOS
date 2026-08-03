import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_cross_organization_tender_access_is_rejected():
    """Depends on dependencies.require_permission being implemented - currently
    raises NotImplementedError (surfaces as 500), not 403, until that TODO is
    filled in."""
    async with await _client() as client:
        admin_resp = await client.post(
            "/auth/register",
            json={
                "email": "cross-admin@example.com",
                "password": "hunter2",
                "organization_name": "Cross Org A",
            },
        )
        assert admin_resp.status_code == 201
        admin_data = admin_resp.json()
        admin_token = admin_data["access_token"]
        org_a_id = admin_data["organization_id"]

        tender_resp = await client.post(
            f"/organizations/{org_a_id}/tenders",
            json={"name": "Confidential RFP"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert tender_resp.status_code == 201
        tender_id = tender_resp.json()["id"]

        outsider_resp = await client.post(
            "/auth/register",
            json={
                "email": "outsider@example.com",
                "password": "hunter2",
                "organization_name": "Cross Org B",
            },
        )
        outsider_token = outsider_resp.json()["access_token"]

        blocked_resp = await client.get(
            f"/tenders/{tender_id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert blocked_resp.status_code == 403
        assert blocked_resp.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_member_of_the_organization_can_access_its_tender():
    async with await _client() as client:
        admin_resp = await client.post(
            "/auth/register",
            json={
                "email": "member-admin@example.com",
                "password": "hunter2",
                "organization_name": "Member Org",
            },
        )
        admin_data = admin_resp.json()
        admin_token = admin_data["access_token"]
        org_id = admin_data["organization_id"]

        tender_resp = await client.post(
            f"/organizations/{org_id}/tenders",
            json={"name": "Team RFP"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        tender_id = tender_resp.json()["id"]

        get_resp = await client.get(
            f"/tenders/{tender_id}", headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Team RFP"
