"""Route-level coverage for readiness.py (G11's readiness pair) - 2 routes,
zero HTTP-level coverage anywhere before this file. Two different actions:
view_readiness (7 roles) for GET, generate_readiness (5 roles - Talent
reviewer/Commercial reviewer excluded, per rbac.py) for POST .../generate -
checked against rbac.py directly, not assumed.

evaluate_readiness reaches graph_service.check_freshness (Neo4j) as part
of its own stale-graph blocker check (readiness_service.py:322-329) - it
already catches GraphStaleError itself and turns it into a real blocker,
unlike the redteam.py bug found earlier this step, so no product fix is
needed here. The route tests still need the same session-per-call Neo4j
fix (tests/route/conftest.py's autouse _no_pooling_for_neo4j/_clean_neo4j)
since check_freshness reaches Neo4j regardless of which branch it takes.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.asyncio


async def test_get_readiness_404s_before_any_report_has_been_generated(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Readiness Route Test Bid"}, headers=headers
    ).json()

    response = client.get(f"/tenders/{tender['id']}/readiness", headers=headers)

    assert response.status_code == 404, response.text


async def test_generate_readiness_persists_and_returns_a_real_report(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Readiness Route Test Bid 2"},
        headers=headers,
    ).json()

    response = client.post(f"/tenders/{tender['id']}/readiness/generate", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tender_id"] == tender["id"]
    assert body["ready"] is False
    # A fresh tender has never had a graph projection - blocker category 7
    # (stale_graph_projection) is expected, not a surprise; confirms
    # readiness_service's own GraphStaleError handling actually ran, not
    # just that the route returned 200.
    assert any(b["category"] == "stale_graph_projection" for b in body["blockers"])


async def test_get_readiness_returns_the_generated_report(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Readiness Route Test Bid 3"},
        headers=headers,
    ).json()
    generated = client.post(
        f"/tenders/{tender['id']}/readiness/generate", headers=headers
    ).json()

    response = client.get(f"/tenders/{tender['id']}/readiness", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == generated["id"]


async def test_generate_readiness_rejects_a_role_outside_generate_readiness(
    client: TestClient, register_actor, add_member_with_role
):
    # generate_readiness's own ACTION_ROLES row is {bid_manager,
    # solution_architect, proposal_writer, approver, admin} -
    # talent_reviewer is a real member (and IS in view_readiness's wider
    # 7-role set) but not in this narrower one.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Readiness Route Test Bid 4"},
        headers=admin_headers,
    ).json()

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    generate_response = client.post(
        f"/tenders/{tender['id']}/readiness/generate", headers=member_headers
    )
    assert generate_response.status_code == 403, generate_response.text

    # Same role IS allowed to view - confirms the two actions are genuinely
    # different gates, not both narrow or both wide by accident.
    view_response = client.get(f"/tenders/{tender['id']}/readiness", headers=member_headers)
    assert view_response.status_code == 404, view_response.text  # allowed through RBAC, no report yet
