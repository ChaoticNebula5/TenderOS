"""Route-level coverage for talent.py (G8) - 3 routes, zero HTTP-level
coverage anywhere before this file. All three share one action
(select_resource) - checked against rbac.py, not assumed: a 4-role
domain-reviewer set (bid_manager/talent_reviewer/approver/admin), the
same shape as approve_compliance/resolve_commercial_finding, not the
workflow-transparency shape the four view_* fixes addressed.

Also where §5's resource_selection targets-mismatch finding (KNOWN_
LIMITATIONS.md) becomes concrete: select-candidate is exercised here
directly, through its own real route - never by round-tripping a gate's
role_requirement-typed targets through target_updates, which
GateDecisionPanel.tsx never attempts for any gate type.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _role_with_candidate(tender_id: str, org_id: str) -> tuple[str, str]:
    """A real role_requirement plus one real role_match against a real
    employee - the minimum select-candidate needs. Not run through the
    talent_analysis_node/LLM pipeline - built directly, the same
    "call the repository, not a live worker/LLM" convention every other
    route test file in this project already uses for its own fixtures.
    """
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=uuid.UUID(org_id),
            name="Dana Whitfield",
            content_fingerprint=uuid.uuid4().hex,
            title="Dana Whitfield",
        )
        role = await uow.talent.create_role_requirement(
            tender_id=uuid.UUID(tender_id),
            role_name="Solution Architect",
            content_fingerprint=uuid.uuid4().hex,
        )
        match = await uow.talent.create_role_match(
            role_requirement_id=role.id, employee_knowledge_item_id=knowledge_item.id
        )
        await uow.commit()
        return str(role.id), str(match.employee_knowledge_item_id)


async def test_list_roles_returns_the_real_role(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid"}, headers=headers
    ).json()
    role_id, _employee_id = await _role_with_candidate(tender["id"], org_id)

    response = client.get(f"/tenders/{tender['id']}/roles", headers=headers)

    assert response.status_code == 200, response.text
    assert role_id in [r["id"] for r in response.json()]


async def test_list_matches_returns_the_real_match(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 2"}, headers=headers
    ).json()
    role_id, employee_id = await _role_with_candidate(tender["id"], org_id)

    response = client.get(f"/roles/{role_id}/matches", headers=headers)

    assert response.status_code == 200, response.text
    assert employee_id in [m["employee_knowledge_item_id"] for m in response.json()]
    assert response.json()[0]["human_selected"] is False


async def test_select_candidate_marks_human_selected(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 3"}, headers=headers
    ).json()
    role_id, employee_id = await _role_with_candidate(tender["id"], org_id)

    response = client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": employee_id, "selected": True},
        headers={**headers, "Idempotency-Key": "select-1"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["human_selected"] is True


async def test_select_candidate_requires_idempotency_key(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 4"}, headers=headers
    ).json()
    role_id, employee_id = await _role_with_candidate(tender["id"], org_id)

    response = client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": employee_id, "selected": True},
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_select_candidate_can_deselect_without_deleting_the_match(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 5"}, headers=headers
    ).json()
    role_id, employee_id = await _role_with_candidate(tender["id"], org_id)
    client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": employee_id, "selected": True},
        headers={**headers, "Idempotency-Key": "select-2a"},
    )

    response = client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": employee_id, "selected": False},
        headers={**headers, "Idempotency-Key": "select-2b"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["human_selected"] is False
    matches = client.get(f"/roles/{role_id}/matches", headers=headers).json()
    assert employee_id in [m["employee_knowledge_item_id"] for m in matches], (
        "deselecting must not delete the role_match row"
    )


async def test_select_candidate_404s_for_an_employee_with_no_match_row(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 6"}, headers=headers
    ).json()
    role_id, _employee_id = await _role_with_candidate(tender["id"], org_id)

    response = client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": str(uuid.uuid4()), "selected": True},
        headers={**headers, "Idempotency-Key": "select-3"},
    )

    assert response.status_code == 404, response.text


async def test_talent_routes_reject_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    # B16 item 3: talent.py's own 3 routes had zero genuine cross-org
    # coverage - test_talent_routes_reject_a_role_outside_select_resource
    # below is a same-org narrower-ROLE case (proposal_writer lacking
    # select_resource), not a different-org case. Real Admin of a
    # DIFFERENT organization for all 3 routes, same shape as this
    # project's other cross-org route tests.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Talent Cross-Org Bid"},
        headers=admin_headers,
    ).json()
    role_id, employee_id = await _role_with_candidate(tender["id"], org_id)

    outsider_headers, _, _outsider_org_id = register_actor()

    list_roles = client.get(f"/tenders/{tender['id']}/roles", headers=outsider_headers)
    assert list_roles.status_code == 403, list_roles.text
    assert list_roles.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    list_matches = client.get(f"/roles/{role_id}/matches", headers=outsider_headers)
    assert list_matches.status_code == 403, list_matches.text
    assert list_matches.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    select_candidate = client.post(
        f"/roles/{role_id}/select-candidate",
        json={"employee_knowledge_item_id": employee_id, "selected": True},
        headers={**outsider_headers, "Idempotency-Key": "select-cross-org"},
    )
    assert select_candidate.status_code == 403, select_candidate.text
    assert select_candidate.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_talent_routes_reject_a_role_outside_select_resource(
    client: TestClient, register_actor, add_member_with_role
):
    # select_resource's own ACTION_ROLES row is {bid_manager,
    # talent_reviewer, approver, admin} - proposal_writer is a real
    # member but not in that set.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Talent Route Test Bid 7"}, headers=admin_headers
    ).json()
    role_id, _employee_id = await _role_with_candidate(tender["id"], org_id)

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "proposal_writer")

    response = client.get(f"/roles/{role_id}/matches", headers=member_headers)

    assert response.status_code == 403, response.text
