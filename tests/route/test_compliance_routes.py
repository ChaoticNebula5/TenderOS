"""Route-level coverage for compliance.py (G7) - 5 routes, zero HTTP-level
coverage anywhere before this file. All five share one action
(approve_compliance) - checked against rbac.py directly, not assumed:
that 4-role set (bid_manager/architect/approver/admin) is a domain-
specific reviewer judgment, the same shape talent.py's select_resource
and commercial.py's resolve_commercial_finding already use, not the
pure-workflow-transparency shape that drove the four view_* fixes -
left as-is here, checked rather than reflexively widened.
`GET /compliance/{id}/trace` DOES use the wider view_compliance_trace
action (7 roles) rather than approve_compliance's 4 - see
test_trace_compliance_is_accessible_to_a_role_outside_approve_compliance
below. (A prior version of this docstring called that widening a
still-open TODO for a later step; it had already landed by the time this
file was finished, and the sentence never got corrected - the exact
"describes a not-yet-true future state that became true" bug CodeRabbit's
review separately caught in test_proposal_routes.py's docstring.)

Fixture setup goes through real service/repository calls, not a live
extraction pipeline - a requirement needs a real document_chunk (a real
FK, not nullable), built directly the same way test_readiness_service.py's
own _chunk/_requirement helpers already do.
"""

import io
import uuid

import docx
import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _chunk(document_id: str) -> str:
    async with UnitOfWork() as uow:
        chunk = await uow.documents.create_chunk(
            document_id=uuid.UUID(document_id),
            chunk_index=0,
            chunk_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
        return str(chunk.id)


async def _requirement_and_compliance_entry(
    tender_id: str, document_id: str, status: str = "not_reviewed"
) -> tuple[str, str]:
    chunk_id = await _chunk(document_id)
    async with UnitOfWork() as uow:
        requirement = await uow.requirements.create(
            tender_id=uuid.UUID(tender_id),
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Bidders must hold a valid tax clearance certificate.",
            normalized_text="bidders must hold a valid tax clearance certificate.",
            category="eligibility",
            mandatory=True,
            source_chunk_id=uuid.UUID(chunk_id),
            content_fingerprint=uuid.uuid4().hex,
        )
        entry = await uow.compliance.create_compliance_entry(
            requirement_id=requirement.id, status=status
        )
        await uow.commit()
        return str(requirement.id), str(entry.id)


async def _make_tender_with_document(client: TestClient, headers: dict, org_id: str) -> tuple[str, str]:
    """A tender plus a real Document row (via the real upload route) -
    requirements need a real source_chunk_id, which needs a real document
    to attach to. No parsing/worker involved - the chunk itself is built
    directly by the test helpers above."""
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Compliance Route Test Bid"}, headers=headers
    ).json()
    document = docx.Document()
    document.add_paragraph("Placeholder RFP content.")
    buf = io.BytesIO()
    document.save(buf)
    upload = client.post(
        f"/tenders/{tender['id']}/documents",
        files={
            "file": (
                "rfp.docx",
                buf.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        headers=headers,
    )
    return tender["id"], upload.json()["id"]


async def test_list_compliance_returns_the_real_entry(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    response = client.get(f"/tenders/{tender_id}/compliance", headers=headers)

    assert response.status_code == 200, response.text
    assert entry_id in [e["id"] for e in response.json()]


async def test_get_compliance_returns_the_real_entry(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    response = client.get(f"/compliance/{entry_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == entry_id
    assert response.json()["human_approved"] is False


async def test_get_compliance_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/compliance/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_patch_compliance_overrides_status_and_sets_human_approved(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(
        tender_id, document_id, status="evidence_unavailable"
    )

    response = client.patch(
        f"/compliance/{entry_id}",
        json={"status": "fully_compliant", "reasoning_summary": "Confirmed via phone call."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "fully_compliant"
    assert response.json()["human_approved"] is True


async def test_approve_compliance_sets_human_approved(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(
        tender_id, document_id, status="fully_compliant"
    )

    response = client.post(f"/compliance/{entry_id}/approve", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["human_approved"] is True


async def test_trace_compliance_walks_requirement_to_entry(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, headers, org_id)
    req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    response = client.get(f"/compliance/{entry_id}/trace", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["compliance_entry_id"] == entry_id
    assert response.json()["requirement_id"] == req_id
    assert response.json()["evidence"] == []


async def test_compliance_routes_reject_a_role_outside_approve_compliance(
    client: TestClient, register_actor, add_member_with_role
):
    # approve_compliance's own ACTION_ROLES row is {bid_manager,
    # solution_architect, approver, admin} - commercial_reviewer is a
    # real member but not in that set.
    admin_headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, admin_headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "commercial_reviewer")

    response = client.get(f"/compliance/{entry_id}", headers=member_headers)

    assert response.status_code == 403, response.text


async def test_trace_compliance_is_accessible_to_a_role_outside_approve_compliance(
    client: TestClient, register_actor, add_member_with_role
):
    # §7 step 10 (B13 plan item e): view_compliance_trace is the new,
    # deliberately wider (7-role) action /trace moved onto - the RBAC
    # target state, not a re-assertion of approve_compliance's old 4-role
    # gate. commercial_reviewer is a real member outside approve_
    # compliance's 4 roles but inside view_compliance_trace's 7 - this
    # is the specific case that shape change exists to unblock (any of
    # the seven roles may be resolving a red-team finding that targets
    # this compliance entry and needs to inspect it).
    admin_headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, admin_headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "commercial_reviewer")

    response = client.get(f"/compliance/{entry_id}/trace", headers=member_headers)

    assert response.status_code == 200, response.text
    assert response.json()["compliance_entry_id"] == entry_id


async def test_trace_compliance_rejects_an_entry_from_another_organization(
    client: TestClient, register_actor
):
    # CodeRabbit review of this block (2 reviews, 27 comments) flagged
    # the four view_* actions (view_workflow/view_gates/
    # view_proposal_trace/view_compliance_trace) as having "no
    # target-organization authorization." True of the route bodies, but
    # incomplete: require_permission("view_compliance_trace")'s own
    # _resolve_organization_id (dependencies.py) resolves
    # compliance_id's REAL owning org and checks membership there before
    # this route ever runs - traced by reading dependencies.py, not
    # previously proven by a real request. The test right above this one
    # proves role BREADTH within one org; this proves org isolation: an
    # Admin of org B (a real, permitted view_compliance_trace role - just
    # in the wrong org) must not reach org A's compliance entry.
    admin_headers, _, org_id = register_actor()
    tender_id, document_id = await _make_tender_with_document(client, admin_headers, org_id)
    _req_id, entry_id = await _requirement_and_compliance_entry(tender_id, document_id)

    other_admin_headers, _, _other_org_id = register_actor()

    response = client.get(f"/compliance/{entry_id}/trace", headers=other_admin_headers)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
