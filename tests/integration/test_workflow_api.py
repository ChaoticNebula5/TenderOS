from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_worker.jobs.workflow import run_workflow

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_and_create_tender(client: AsyncClient, email: str, org_name: str) -> tuple:
    register_resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = register_resp.json()
    token = data["access_token"]
    tender_resp = await client.post(
        f"/organizations/{data['organization_id']}/tenders",
        json={"name": "API Test RFP"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, tender_resp.json()["id"]


async def test_create_workflow_run_requires_idempotency_key():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "wf-api-1@example.com", "WF API Org 1"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/workflow-runs",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_create_workflow_run_succeeds_and_is_idempotent():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "wf-api-2@example.com", "WF API Org 2"
        )
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-run-1"}

        first = await client.post(f"/tenders/{tender_id}/workflow-runs", headers=headers)
        second = await client.post(f"/tenders/{tender_id}/workflow-runs", headers=headers)

        assert first.status_code == 202
        assert first.json()["tender_id"] == tender_id
        assert first.json()["status"] == "queued"
        assert second.status_code == 202
        assert second.json()["id"] == first.json()["id"], "replay must return the same run"


async def test_cancel_workflow_run_requires_the_new_rbac_action():
    async with await _client() as client:
        admin_token, tender_id = await _register_and_create_tender(
            client, "wf-api-cancel-admin@example.com", "WF API Cancel Org"
        )
        run_resp = await client.post(
            f"/tenders/{tender_id}/workflow-runs",
            headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "cancel-run-1"},
        )
        run_id = run_resp.json()["id"]

        # A talent_reviewer is a real org member but cancel_workflow_run's
        # ACTION_ROLES row is {bid_manager, approver, admin} - not this role.
        outsider_resp = await client.post(
            "/auth/register",
            json={
                "email": "wf-api-outsider@example.com",
                "password": "hunter2",
                "organization_name": "Outsider Org",
            },
        )
        outsider_token = outsider_resp.json()["access_token"]

        blocked = await client.post(
            f"/workflow-runs/{run_id}/cancel",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert blocked.status_code == 403

        allowed = await client.post(
            f"/workflow-runs/{run_id}/cancel",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "cancelled"


async def test_gate_decision_via_http_uses_the_gate_types_own_rbac_action():
    """requirement_review's action is review_requirements - a role with real
    org membership but the wrong permission for THIS gate_type must still be
    rejected, proving require_gate_decision_permission resolves the action
    from the gate itself rather than some fixed route-level permission."""
    async with await _client() as client:
        admin_token, tender_id = await _register_and_create_tender(
            client, "wf-api-gate-admin@example.com", "WF API Gate Org"
        )
        run_resp = await client.post(
            f"/tenders/{tender_id}/workflow-runs",
            headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "gate-run-1"},
        )
        run_id = run_resp.json()["id"]

        # Drive the graph to its first pause directly (no live RQ worker in
        # this test process) so there's a real gate to decide via HTTP.
        await run_workflow(run_id)
        async with UnitOfWork() as uow:
            gates = await uow.gates.list_for_run(UUID(run_id))
        gate_id = str(gates[0].id)

        no_key_resp = await client.post(
            f"/gates/{gate_id}/decision",
            json={"decision": "approve"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert no_key_resp.status_code == 400
        assert no_key_resp.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"

        decide_resp = await client.post(
            f"/gates/{gate_id}/decision",
            json={"decision": "approve", "notes": "looks fine"},
            headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "decide-1"},
        )
        assert decide_resp.status_code == 200
        assert decide_resp.json()["status"] == "approved"

        replay_resp = await client.post(
            f"/gates/{gate_id}/decision",
            json={"decision": "approve", "notes": "looks fine"},
            headers={"Authorization": f"Bearer {admin_token}", "Idempotency-Key": "decide-1"},
        )
        assert replay_resp.status_code == 200
        assert replay_resp.json()["gate_id"] == gate_id
