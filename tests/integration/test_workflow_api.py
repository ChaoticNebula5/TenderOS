import io
from uuid import UUID

import docx
import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.orchestration.nodes import requirement_extraction
from tenderos_worker.jobs.ingestion import parse_and_embed_document
from tenderos_worker.jobs.workflow import run_workflow

pytestmark = pytest.mark.asyncio

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Placeholder content for workflow-run API test fixtures.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _register_and_create_tender(client: AsyncClient, email: str, org_name: str) -> tuple:
    """Also uploads and fully ingests one document, via the real HTTP upload
    route, then a direct parse_and_embed_document call since no live worker
    runs in this test process - create_workflow_run now refuses to start
    until every document has parse_status='parsed' (DocumentsNotReadyError).
    """
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
    tender_id = tender_resp.json()["id"]

    upload_resp = await client.post(
        f"/tenders/{tender_id}/documents",
        files={"file": ("placeholder.docx", _docx_bytes(), _DOCX_TYPE)},
        headers={"Authorization": f"Bearer {token}"},
    )
    await parse_and_embed_document(upload_resp.json()["id"])

    return token, tender_id


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


async def test_gate_decision_via_http_uses_the_gate_types_own_rbac_action(monkeypatch):
    """requirement_review's action is review_requirements - a role with real
    org membership but the wrong permission for THIS gate_type must still be
    rejected, proving require_gate_decision_permission resolves the action
    from the gate itself rather than some fixed route-level permission."""
    fake_client = FakeLLMClient()
    fake_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [ExtractionOutput(requirements=[], submission_items=[], clarifications=[])],
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)

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


async def test_stream_workflow_events_rejects_a_caller_outside_the_organization():
    # The route's own dependency chain (require_permission_for_sse) rejects
    # before _workflow_progress_stream's generator ever starts - this
    # exercises real HTTP + real RBAC without needing an incremental read
    # of a live stream, which httpx.ASGITransport cannot do for this route
    # at all (see the generator-level test in test_workflow_events_stream.py
    # for why, and for the real, direct proof the stream itself works).
    async with await _client() as client:
        owner_token, tender_id = await _register_and_create_tender(
            client, "wf-api-sse-403-owner@example.com", "WF API SSE 403 Org"
        )
        run_resp = await client.post(
            f"/tenders/{tender_id}/workflow-runs",
            headers={"Authorization": f"Bearer {owner_token}", "Idempotency-Key": "sse-403-1"},
        )
        run_id = run_resp.json()["id"]

        outsider = await client.post(
            "/auth/register",
            json={
                "email": "wf-api-sse-403-outsider@example.com",
                "password": "hunter2",
                "organization_name": "WF API SSE 403 Outsider Org",
            },
        )
        outsider_token = outsider.json()["access_token"]

        response = await client.get(
            f"/workflow-runs/{run_id}/events/stream",
            params={"access_token": outsider_token},
        )

        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
