"""Route-level coverage for proposal.py (G10, plus B13's own claims-list
addition) - 5 routes now, zero HTTP-level coverage anywhere before this
file. Four of the five share one action (edit_proposal, 4 roles:
bid_manager/solution_architect/proposal_writer/admin) - checked against
rbac.py, not assumed - except GET /proposal/claims/{id}/trace, which
§7 step 10 moved onto the wider 7-role view_proposal_trace action (see
test_trace_proposal_claim_is_accessible_to_a_role_outside_edit_proposal
below).

Phase 4 Stage 2: generate_proposal no longer drafts inside the request. It
records a proposal_generation async operation and returns 202; the worker's
run_operation drafts. _generate below runs that worker call in-process,
with the worker's _build_llm_client monkeypatched to FakeLLMClient, then
reads the sections back through GET .../proposal/sections, so every test
that needs a drafted section still goes through the real route and job.
Fixtures target a brand-new tender with zero requirements/compliance/roles
on purpose: draft_proposal_for_tender only drafts a section with no
underlying facts when the template is `always_on` (SECTION_TEMPLATES'
"Company Overview" is the only one), so a fresh tender deterministically
produces exactly one section and exactly one scripted LLM call - no
compliance/talent fixture setup needed to exercise this route honestly.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.proposal import ProposalClaimDraft, ProposalSectionDraft
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs.operations import run_operation

pytestmark = pytest.mark.asyncio


def _script_company_overview(fake_client: FakeLLMClient) -> None:
    fake_client.script(
        "proposal_drafting",
        ProposalSectionDraft,
        [
            ProposalSectionDraft(
                content="Acme Corp is well suited to deliver this engagement.",
                claims=[
                    ProposalClaimDraft(
                        claim_text="Acme Corp is well suited to deliver this engagement.",
                        claim_type="other",
                        is_factual=False,
                    )
                ],
            )
        ],
    )


async def _generate(client: TestClient, headers: dict, tender_id: str, monkeypatch) -> list:
    fake_client = FakeLLMClient()
    _script_company_overview(fake_client)
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake_client)
    response = client.post(
        f"/tenders/{tender_id}/proposal/generate",
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert response.status_code == 202, response.text
    # What the worker does when the outbox delivers OperationRequested.
    await run_operation(response.json()["operation_id"])
    operation = client.get(f"/operations/{response.json()['operation_id']}", headers=headers)
    assert operation.json()["status"] == "succeeded", operation.text
    sections = client.get(f"/tenders/{tender_id}/proposal/sections", headers=headers)
    assert sections.status_code == 200, sections.text
    return sections.json()


async def test_generate_proposal_drafts_exactly_the_always_on_section(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid"}, headers=headers
    ).json()

    sections = await _generate(client, headers, tender["id"], monkeypatch)

    assert len(sections) == 1
    assert sections[0]["section_name"] == "Company Overview"
    assert sections[0]["status"] == "draft"


async def test_generate_proposal_returns_202_and_the_same_operation_while_active(
    client: TestClient, register_actor
):
    """Phase 4 Stage 2: the POST only records the operation. A retry with the
    same Idempotency-Key replays the 202; a second click (a new key) while
    the first is still queued gets the same operation, not a second one."""
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Proposal Route Test Bid 12"},
        headers=headers,
    ).json()
    key = str(uuid.uuid4())
    url = f"/tenders/{tender['id']}/proposal/generate"

    first = client.post(url, headers={**headers, "Idempotency-Key": key})
    replayed = client.post(url, headers={**headers, "Idempotency-Key": key})
    second_click = client.post(url, headers={**headers, "Idempotency-Key": str(uuid.uuid4())})

    assert first.status_code == 202, first.text
    body = first.json()
    assert (body["kind"], body["status"], body["tender_id"]) == (
        "proposal_generation",
        "queued",
        tender["id"],
    )
    assert replayed.status_code == 202, replayed.text
    assert replayed.json() == body
    assert second_click.status_code == 202, second_click.text
    assert second_click.json()["operation_id"] == body["operation_id"]
    # Nothing was drafted in the request.
    sections = client.get(f"/tenders/{tender['id']}/proposal/sections", headers=headers)
    assert sections.json() == []


async def test_generate_proposal_requires_idempotency_key(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 2"}, headers=headers
    ).json()

    response = client.post(f"/tenders/{tender['id']}/proposal/generate", headers=headers)

    assert response.status_code == 400, response.text


async def test_list_proposal_sections_returns_the_real_section(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 3"}, headers=headers
    ).json()
    await _generate(client, headers, tender["id"], monkeypatch)

    response = client.get(f"/tenders/{tender['id']}/proposal/sections", headers=headers)

    assert response.status_code == 200, response.text
    assert [s["section_name"] for s in response.json()] == ["Company Overview"]


async def test_list_proposal_claims_returns_the_real_claim(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 4"}, headers=headers
    ).json()
    sections = await _generate(client, headers, tender["id"], monkeypatch)
    section_id = sections[0]["id"]

    response = client.get(f"/proposal/sections/{section_id}/claims", headers=headers)

    assert response.status_code == 200, response.text
    claims = response.json()
    assert len(claims) == 1
    assert claims[0]["section_id"] == section_id
    assert claims[0]["grounding_status"] == "not_required"


async def test_patch_proposal_section_updates_content(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 5"}, headers=headers
    ).json()
    sections = await _generate(client, headers, tender["id"], monkeypatch)
    section_id = sections[0]["id"]

    response = client.patch(
        f"/proposal/sections/{section_id}",
        json={"content": "A human-edited introduction."},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == "A human-edited introduction."


async def test_patch_proposal_section_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.patch(
        f"/proposal/sections/{uuid.uuid4()}", json={"content": "x"}, headers=headers
    )

    assert response.status_code == 404, response.text


async def test_trace_proposal_claim_walks_claim_to_section(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 6"}, headers=headers
    ).json()
    sections = await _generate(client, headers, tender["id"], monkeypatch)
    section_id = sections[0]["id"]
    claim_id = client.get(
        f"/proposal/sections/{section_id}/claims", headers=headers
    ).json()[0]["id"]

    response = client.get(f"/proposal/claims/{claim_id}/trace", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["claim_id"] == claim_id
    assert body["section_id"] == section_id
    assert body["evidence"] == []
    # Company Overview is SECTION_TEMPLATES' one deliberately-not-data-gated
    # template (proposal_service.py:740) - the drawer needs this false to
    # render "this section isn't data-gated" instead of an empty list.
    assert body["section_is_data_gated"] is False


async def test_trace_proposal_claim_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/proposal/claims/{uuid.uuid4()}/trace", headers=headers)

    assert response.status_code == 404, response.text


async def test_proposal_routes_reject_a_role_outside_edit_proposal(
    client: TestClient, register_actor, add_member_with_role
):
    # edit_proposal's own ACTION_ROLES row is {bid_manager,
    # solution_architect, proposal_writer, admin} - commercial_reviewer is a
    # real member but not in that set.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Proposal Route Test Bid 7"},
        headers=admin_headers,
    ).json()

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "commercial_reviewer")

    response = client.get(f"/tenders/{tender['id']}/proposal/sections", headers=member_headers)

    assert response.status_code == 403, response.text


async def test_trace_proposal_claim_is_accessible_to_a_role_outside_edit_proposal(
    client: TestClient, register_actor, add_member_with_role, monkeypatch
):
    # §7 step 10 (B13 plan item e): view_proposal_trace is the new,
    # deliberately wider (7-role) action /trace moved onto - the RBAC
    # target state, not a re-assertion of edit_proposal's old 4-role
    # gate. talent_reviewer is a real member outside edit_proposal's 4
    # roles but inside view_proposal_trace's 7 - this is the specific
    # case that shape change exists to unblock (any of the seven roles
    # may be resolving a red-team finding that targets this claim and
    # needs to inspect it, or an Approver deciding final_gate needs to
    # see why a claim is unsupported).
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Proposal Route Test Bid 8"},
        headers=admin_headers,
    ).json()
    sections = await _generate(client, admin_headers, tender["id"], monkeypatch)
    claim_id = client.get(
        f"/proposal/sections/{sections[0]['id']}/claims", headers=admin_headers
    ).json()[0]["id"]

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    response = client.get(f"/proposal/claims/{claim_id}/trace", headers=member_headers)

    assert response.status_code == 200, response.text
    assert response.json()["claim_id"] == claim_id


async def test_list_proposal_claims_rejects_a_section_from_another_organization(
    client: TestClient, register_actor, monkeypatch
):
    # CodeRabbit review of this block (2 reviews, 27 comments) flagged
    # list_proposal_claims as having "no org check" because the route
    # body itself never resolves section->org. That's true of the route
    # body, but incomplete: require_permission(_EDIT)'s own
    # _resolve_organization_id (dependencies.py) resolves section_id's
    # REAL owning org and checks membership there before this route ever
    # runs - traced by reading dependencies.py, not previously proven by
    # a real request. This is that proof: an Admin of org B (a real,
    # permitted edit_proposal role - just in the wrong org) must not
    # reach org A's claims.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 9"}, headers=admin_headers
    ).json()
    sections = await _generate(client, admin_headers, tender["id"], monkeypatch)

    other_admin_headers, _, _other_org_id = register_actor()

    response = client.get(
        f"/proposal/sections/{sections[0]['id']}/claims", headers=other_admin_headers
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_trace_proposal_claim_rejects_a_claim_from_another_organization(
    client: TestClient, register_actor, monkeypatch
):
    # Same proof as test_list_proposal_claims_rejects_a_section_from_
    # another_organization, for view_proposal_trace specifically - the
    # existing "accessible to a role outside edit_proposal" test above
    # proves role BREADTH within one org, not org isolation; this is the
    # property CodeRabbit's review asked to see verified with a real
    # request rather than trusted from tracing dependencies.py alone.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Proposal Route Test Bid 10"}, headers=admin_headers
    ).json()
    sections = await _generate(client, admin_headers, tender["id"], monkeypatch)
    claim_id = client.get(
        f"/proposal/sections/{sections[0]['id']}/claims", headers=admin_headers
    ).json()[0]["id"]

    other_admin_headers, _, _other_org_id = register_actor()

    response = client.get(f"/proposal/claims/{claim_id}/trace", headers=other_admin_headers)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_list_generate_and_patch_reject_a_caller_outside_the_organization(
    client: TestClient, register_actor, monkeypatch
):
    # B16 item 3: the other 3 proposal.py routes had zero cross-org
    # coverage - only claims-list and trace (above) did. Same shape:
    # a real Admin of a DIFFERENT organization. generate_proposal's own
    # RBAC dependency runs before the route body, so the 403 comes before
    # any operation is recorded.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Proposal Route Test Bid 11"},
        headers=admin_headers,
    ).json()
    sections = await _generate(client, admin_headers, tender["id"], monkeypatch)

    outsider_headers, _, _outsider_org_id = register_actor()

    list_resp = client.get(
        f"/tenders/{tender['id']}/proposal/sections", headers=outsider_headers
    )
    assert list_resp.status_code == 403, list_resp.text
    assert list_resp.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    generate_resp = client.post(
        f"/tenders/{tender['id']}/proposal/generate",
        headers={**outsider_headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert generate_resp.status_code == 403, generate_resp.text
    assert generate_resp.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    patch_resp = client.patch(
        f"/proposal/sections/{sections[0]['id']}",
        json={"content": "Hijacked content"},
        headers=outsider_headers,
    )
    assert patch_resp.status_code == 403, patch_resp.text
    assert patch_resp.json()["error"]["code"] == "ORG_ACCESS_DENIED"
