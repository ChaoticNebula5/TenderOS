"""Proves the harness's own RBAC mechanism, not a specific route's business
logic. Every one of the other 14 route-test modules authenticates through
register_actor and asserts narrower-role rejections through
add_member_with_role - if either silently granted a broader role than
requested, every "this route rejects role X" assertion downstream would
pass vacuously rather than fail loudly, the same shape
set_candidate_selection's own test had (a comparison proven correct in
isolation, against a state no real caller could ever produce). One test
here protects every RBAC assertion built on top of it."""

import uuid

from fastapi.testclient import TestClient


def test_register_actor_and_narrower_role_is_actually_rejected(
    client: TestClient, register_actor, add_member_with_role
):
    # create_bid (Bid manager / Admin only, Part H) needs no setup beyond
    # org_id - the simplest real route to isolate this from any other
    # resource's own RBAC quirks.
    admin_headers, _, org_id = register_actor()
    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)

    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    response = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Should Be Rejected"},
        headers=member_headers,
    )

    assert response.status_code == 403, response.text

    # And the positive case, in the same test - a role that IS in
    # create_bid's allowed set (bid_manager) must succeed through the
    # identical membership mechanism, so a bug that rejected everyone
    # regardless of role wouldn't hide behind the negative assertion alone.
    manager_email = f"{uuid.uuid4()}@example.com"
    manager_headers, _, _ = register_actor(email=manager_email)
    add_member_with_role(admin_headers, org_id, manager_email, "bid_manager")

    response = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Should Be Allowed"},
        headers=manager_headers,
    )

    assert response.status_code == 201, response.text
