"""Route-level coverage for redteam.py (G11's 2 documented findings routes,
plus the undocumented-in-Part-G but real POST .../redteam/run - see
KNOWN_LIMITATIONS.md's own entry on that doc gap) - 3 routes, zero
HTTP-level coverage anywhere before this file. All three share one action
(resolve_redteam_finding, all 7 roles - the widest action in the whole
matrix per rbac.py's own comment) - checked against rbac.py, not assumed.
Since every organization member role has access, there's no narrower-role
rejection test to write here (unlike compliance.py/talent.py/commercial.py);
the real-member-vs-non-member distinction is tested instead, matching
test_graph_routes.py's own pattern for the other all-7-role actions.

Phase 4: trigger_red_team no longer runs the red team (or calls the LLM)
inside the request. It records a redteam_run async operation and returns
202; the worker runs it. So no LLM client is patched here any more; the
run itself is covered by tests/integration/test_async_operations.py and
the operation routes by tests/integration/test_operations_api.py.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _seed_finding(tender_id: str) -> str:
    async with UnitOfWork() as uow:
        finding = await uow.redteam.create_finding(
            tender_id=uuid.UUID(tender_id),
            target_type="proposal_claim",
            target_id=None,
            finding_category="unsupported_claim",
            severity="major",
            finding_text="Claim asserts a certification with no cited evidence.",
            violated_condition="claim.grounding_status == 'unsupported'",
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return str(finding.id)


async def test_trigger_red_team_queues_an_operation_and_returns_202(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Redteam Route Test Bid"}, headers=headers
    ).json()

    response = client.post(f"/tenders/{tender['id']}/redteam/run", headers=headers)

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "redteam_run"
    assert body["tender_id"] == tender["id"]
    uuid.UUID(body["operation_id"])


async def test_list_redteam_findings_returns_the_real_finding(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Redteam Route Test Bid 2"},
        headers=headers,
    ).json()
    finding_id = await _seed_finding(tender["id"])

    response = client.get(f"/tenders/{tender['id']}/redteam-findings", headers=headers)

    assert response.status_code == 200, response.text
    assert finding_id in [f["id"] for f in response.json()]


async def test_resolve_redteam_finding_sets_resolved_and_note(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Redteam Route Test Bid 3"},
        headers=headers,
    ).json()
    finding_id = await _seed_finding(tender["id"])

    response = client.post(
        f"/redteam-findings/{finding_id}/resolve",
        json={"resolution_note": "Evidence added and re-verified."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["resolved"] is True
    assert response.json()["resolution_note"] == "Evidence added and re-verified."


async def test_resolve_redteam_finding_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.post(f"/redteam-findings/{uuid.uuid4()}/resolve", json={}, headers=headers)

    assert response.status_code == 404, response.text


async def test_redteam_routes_reject_a_non_member(client: TestClient, register_actor):
    # resolve_redteam_finding is granted to all 7 organizational roles -
    # every real member has access, so the real distinction to test is
    # member vs. non-member, not a narrower role.
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Redteam Route Test Bid 4"},
        headers=headers,
    ).json()
    await _seed_finding(tender["id"])

    outsider_headers, _, _ = register_actor()

    response = client.get(f"/tenders/{tender['id']}/redteam-findings", headers=outsider_headers)
    assert response.status_code == 403, response.text


async def test_trigger_red_team_rejects_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Redteam Route Test Bid 5"},
        headers=headers,
    ).json()

    outsider_headers, _, _ = register_actor()

    response = client.post(f"/tenders/{tender['id']}/redteam/run", headers=outsider_headers)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_resolve_redteam_finding_rejects_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Redteam Route Test Bid 6"},
        headers=headers,
    ).json()
    finding_id = await _seed_finding(tender["id"])

    outsider_headers, _, _ = register_actor()

    response = client.post(
        f"/redteam-findings/{finding_id}/resolve",
        json={"resolution_note": "Should not be allowed."},
        headers=outsider_headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    response = client.post(
        f"/redteam-findings/{finding_id}/resolve", json={}, headers=outsider_headers
    )
    assert response.status_code == 403, response.text
