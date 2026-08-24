"""Route-level coverage for workflow.py (G3) - the genuine gaps, plus the
view_workflow RBAC retrofit. POST /tenders/{id}/workflow-runs and
POST /workflow-runs/{id}/cancel already have real HTTP-level coverage in
tests/integration/test_workflow_api.py (httpx.AsyncClient/ASGITransport,
predating B13 - missed during this block's own original planning
inventory, corrected here rather than silently duplicated).
GET /workflow-runs/{id}, .../tasks, .../events, and the new
GET /tenders/{id}/workflow-runs had none anywhere before this file.

All four GET routes moved from create_bid (bid_manager/admin only) to the
new view_workflow action (all seven roles - see rbac.py's own rationale).
The RBAC tests below assert the corrected behavior directly - a Talent
reviewer succeeds, a non-member is rejected - not the old create_bid
boundary reworded to still pass. Asserting whatever the code currently
does, rather than what it should do, is exactly how
set_candidate_selection's own RBAC bug passed its test twice while
broken."""

import io
import uuid

import docx
import pytest
from fastapi.testclient import TestClient
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Placeholder content for workflow route test fixtures.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _tender_with_parsed_document(client: TestClient, headers: dict, org_id: str) -> str:
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Workflow Route Test Bid"},
        headers=headers,
    ).json()
    upload = client.post(
        f"/tenders/{tender['id']}/documents",
        files={"file": ("rfp.docx", _docx_bytes(), _DOCX_TYPE)},
        headers=headers,
    )
    # No live worker in this test process - same direct call
    # test_workflow_api.py's own fixture already established.
    await parse_and_embed_document(upload.json()["id"])
    return tender["id"]


async def test_create_workflow_run_rejects_when_documents_are_not_parsed(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "No Documents Bid"}, headers=headers
    ).json()

    response = client.post(
        f"/tenders/{tender['id']}/workflow-runs",
        headers={**headers, "Idempotency-Key": "no-docs-run"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DOCUMENTS_NOT_READY"


async def test_get_workflow_run_returns_the_real_run(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, headers, org_id)
    created = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**headers, "Idempotency-Key": "get-run-1"},
    ).json()

    response = client.get(f"/workflow-runs/{created['id']}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["tender_id"] == tender_id
    assert response.json()["status"] == "queued"


async def test_get_workflow_run_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/workflow-runs/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_list_workflow_tasks_returns_an_empty_list_before_any_task_exists(
    client: TestClient, register_actor
):
    # No live RQ worker in this test process - the run is created but never
    # actually driven through the graph, so an empty list (not a 404 or
    # error) is the real, correct response at this point.
    headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, headers, org_id)
    created = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**headers, "Idempotency-Key": "list-tasks-1"},
    ).json()

    response = client.get(f"/workflow-runs/{created['id']}/tasks", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_list_workflow_events_includes_the_creation_audit_event(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, headers, org_id)
    created = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**headers, "Idempotency-Key": "list-events-1"},
    ).json()

    response = client.get(f"/workflow-runs/{created['id']}/events", headers=headers)

    assert response.status_code == 200, response.text
    actions = [event["action"] for event in response.json()]
    assert "create_workflow_run" in actions


async def test_list_workflow_runs_returns_all_runs_for_the_tender_newest_first(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, headers, org_id)
    first = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**headers, "Idempotency-Key": "list-runs-1"},
    ).json()
    client.post(f"/workflow-runs/{first['id']}/cancel", headers=headers)
    second = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**headers, "Idempotency-Key": "list-runs-2"},
    ).json()

    response = client.get(f"/tenders/{tender_id}/workflow-runs", headers=headers)

    assert response.status_code == 200, response.text
    ids = [run["id"] for run in response.json()]
    assert ids == [second["id"], first["id"]], "must be newest-first, both runs present"


async def test_workflow_run_routes_are_readable_by_every_role_that_is_a_real_member(
    client: TestClient, register_actor, add_member_with_role
):
    # view_workflow's own point: a Talent reviewer - one of the five roles
    # create_bid used to lock out - must succeed, not be rejected. This is
    # the corrected behavior itself, not a boundary check around it.
    admin_headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, admin_headers, org_id)
    created = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**admin_headers, "Idempotency-Key": "member-role-run"},
    ).json()

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    for path in (
        f"/tenders/{tender_id}/workflow-runs",
        f"/workflow-runs/{created['id']}",
        f"/workflow-runs/{created['id']}/tasks",
        f"/workflow-runs/{created['id']}/events",
    ):
        response = client.get(path, headers=member_headers)
        assert response.status_code == 200, f"{path}: {response.text}"


async def test_workflow_run_routes_reject_a_non_member(client: TestClient, register_actor):
    # view_workflow's real seven-role breadth means there is no role left
    # to test rejection with - all seven roles that exist are in the set.
    # The only rejection left is someone who isn't a member of THIS
    # organization - which register_actor's own second call proves as a
    # genuine cross-org case, not an unauthenticated stranger:
    # auth_service.register always creates a full org+ADMIN membership
    # together, so outsider_headers below belongs to a real Admin (a
    # permitted role) of a DIFFERENT organization. This is the same
    # "target-org authorization for the broad view actions" property
    # CodeRabbit's review asked to have verified with a real request
    # rather than trusted from reading dependencies.py's
    # _resolve_organization_id/_check_permission chain alone.
    admin_headers, _, org_id = register_actor()
    tender_id = await _tender_with_parsed_document(client, admin_headers, org_id)
    created = client.post(
        f"/tenders/{tender_id}/workflow-runs",
        headers={**admin_headers, "Idempotency-Key": "non-member-run"},
    ).json()

    outsider_headers, _, _ = register_actor()

    for path in (
        f"/tenders/{tender_id}/workflow-runs",
        f"/workflow-runs/{created['id']}",
        f"/workflow-runs/{created['id']}/tasks",
        f"/workflow-runs/{created['id']}/events",
    ):
        response = client.get(path, headers=outsider_headers)
        assert response.status_code == 403, f"{path}: {response.text}"
        assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
