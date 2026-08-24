"""Route-level coverage for organizations.py (G2's tender creation half +
the membership route) and tenders.py (G2's single-tender get/patch) -
§3a's full-module commitment, not just what screens 1/5/6 happen to call.
add_membership and PATCH /tenders/{id} are both in the "no screen touches
it" category of §3a's inventory - standalone coverage here is the only
thing exercising them at all."""

import uuid

from fastapi.testclient import TestClient


def test_create_tender_succeeds_for_bid_manager(client: TestClient, register_actor):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "A New Bid", "client_name": "Acme Corp"},
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "A New Bid"
    assert body["client_name"] == "Acme Corp"
    assert body["organization_id"] == org_id
    assert body["state_version"] == 0


def test_create_tender_rejects_a_narrower_role(
    client: TestClient, register_actor, add_member_with_role
):
    admin_headers, _, org_id = register_actor()
    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    response = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Should Fail"}, headers=member_headers
    )

    assert response.status_code == 403, response.text


def test_list_tenders_returns_only_that_organizations_tenders(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    other_headers, _, other_org_id = register_actor()

    client.post(f"/organizations/{org_id}/tenders", json={"name": "Org A Bid"}, headers=headers)
    client.post(
        f"/organizations/{other_org_id}/tenders", json={"name": "Org B Bid"}, headers=other_headers
    )

    response = client.get(f"/organizations/{org_id}/tenders", headers=headers)

    assert response.status_code == 200, response.text
    names = [t["name"] for t in response.json()]
    assert names == ["Org A Bid"]


def test_add_membership_succeeds_for_a_real_existing_user(client: TestClient, register_actor):
    admin_headers, _, org_id = register_actor()
    member_email = f"{uuid.uuid4()}@example.com"
    _, member_user_id, _ = register_actor(email=member_email)

    response = client.post(
        f"/organizations/{org_id}/memberships",
        json={"email": member_email, "role": "commercial_reviewer"},
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user_id"] == member_user_id
    assert body["organization_id"] == org_id
    assert body["role"] == "commercial_reviewer"


def test_add_membership_rejects_an_unregistered_email(client: TestClient, register_actor):
    admin_headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/memberships",
        json={"email": "nobody@example.com", "role": "commercial_reviewer"},
        headers=admin_headers,
    )

    assert response.status_code == 404, response.text


def test_add_membership_rejects_an_already_existing_membership(client: TestClient, register_actor):
    admin_headers, _, org_id = register_actor()
    member_email = f"{uuid.uuid4()}@example.com"
    register_actor(email=member_email)

    first = client.post(
        f"/organizations/{org_id}/memberships",
        json={"email": member_email, "role": "commercial_reviewer"},
        headers=admin_headers,
    )
    assert first.status_code == 201, first.text

    second = client.post(
        f"/organizations/{org_id}/memberships",
        json={"email": member_email, "role": "talent_reviewer"},
        headers=admin_headers,
    )

    assert second.status_code == 409, second.text


def test_add_membership_requires_manage_users_roles(
    client: TestClient, register_actor, add_member_with_role
):
    # manage_users_roles (Part H) is Admin-only - not even Bid manager.
    admin_headers, _, org_id = register_actor()
    manager_email = f"{uuid.uuid4()}@example.com"
    manager_headers, _, _ = register_actor(email=manager_email)
    add_member_with_role(admin_headers, org_id, manager_email, "bid_manager")

    target_email = f"{uuid.uuid4()}@example.com"
    register_actor(email=target_email)

    response = client.post(
        f"/organizations/{org_id}/memberships",
        json={"email": target_email, "role": "talent_reviewer"},
        headers=manager_headers,
    )

    assert response.status_code == 403, response.text


def test_get_tender_returns_the_real_tender(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    created = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Fetchable Bid"}, headers=headers
    ).json()

    response = client.get(f"/tenders/{created['id']}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Fetchable Bid"


def test_get_tender_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/tenders/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


def test_patch_tender_updates_the_named_fields(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    created = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Original Name"}, headers=headers
    ).json()

    response = client.patch(
        f"/tenders/{created['id']}",
        json={"name": "Updated Name", "client_name": "New Client"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Updated Name"
    assert body["client_name"] == "New Client"


def test_patch_tender_rejects_a_narrower_role(
    client: TestClient, register_actor, add_member_with_role
):
    admin_headers, _, org_id = register_actor()
    created = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Protected Bid"}, headers=admin_headers
    ).json()
    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "proposal_writer")

    response = client.patch(
        f"/tenders/{created['id']}", json={"name": "Hijacked"}, headers=member_headers
    )

    assert response.status_code == 403, response.text
