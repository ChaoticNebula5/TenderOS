"""Route-level coverage for commercial.py (G9) - 4 routes, zero HTTP-level
coverage anywhere before this file. All four share one action
(resolve_commercial_finding) - checked against rbac.py, not assumed: a
4-role domain-reviewer set (bid_manager/commercial_reviewer/approver/
admin), the same shape as approve_compliance/select_resource, not the
workflow-transparency shape the four view_* fixes addressed.

Phase 4 Stage 2: validate_pricing_document no longer runs the checks or
their explanation LLM calls inside the request. It records a
commercial_validation async operation for the document's tender and
returns 202; the worker's run_operation does the work. The tests below run
that worker call in-process, with the worker's _build_llm_client
monkeypatched to FakeLLMClient, matching every other LLM-adjacent test in
this project.
"""

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import commercial_service, document_service
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs.operations import run_operation

pytestmark = pytest.mark.asyncio

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"


async def _ingest_pricing_sheet(org_id: str, tender_id: str, filename: str) -> str:
    content = (FIXTURES_DIR / filename).read_bytes()
    document, _is_new = await document_service.create_document(
        tender_id=uuid.UUID(tender_id),
        organization_id=uuid.UUID(org_id),
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    pricing_documents = await commercial_service.ingest_pricing_workbook(
        document.id, uuid.UUID(tender_id)
    )
    return str(pricing_documents[0].id)


async def _make_pricing_finding(tender_id: str) -> str:
    async with UnitOfWork() as uow:
        finding = await uow.commercial.create_pricing_finding(
            tender_id=uuid.UUID(tender_id),
            finding_type="wrong_total",
            note="Line total mismatch.",
            content_fingerprint=uuid.uuid4().hex,
            severity="major",
        )
        await uow.commit()
        return str(finding.id)


async def test_list_pricing_documents_returns_the_real_document(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Commercial Route Test Bid"}, headers=headers
    ).json()
    pricing_document_id = await _ingest_pricing_sheet(org_id, tender["id"], "pricing_schedule_1.xlsx")

    response = client.get(f"/tenders/{tender['id']}/pricing-documents", headers=headers)

    assert response.status_code == 200, response.text
    assert pricing_document_id in [d["id"] for d in response.json()]


async def test_validate_pricing_document_queues_an_operation_the_worker_runs(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Commercial Route Test Bid 2"}, headers=headers
    ).json()
    pricing_document_id = await _ingest_pricing_sheet(org_id, tender["id"], "pricing_schedule_1.xlsx")

    fake_client = FakeLLMClient()
    fake_client.script(
        "commercial_explanation",
        FindingExplanation,
        [FindingExplanation(severity="major", explanation="Deterministically identified.")] * 8,
    )
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake_client)
    url = f"/pricing-documents/{pricing_document_id}/validate"

    response = client.post(url, headers=headers)
    second_click = client.post(url, headers=headers)

    assert response.status_code == 202, response.text
    body = response.json()
    assert (body["kind"], body["status"], body["tender_id"]) == (
        "commercial_validation",
        "queued",
        tender["id"],
    )
    # A second click while the first is queued joins it.
    assert second_click.status_code == 202, second_click.text
    assert second_click.json()["operation_id"] == body["operation_id"]
    # Nothing ran in the request.
    assert client.get(f"/tenders/{tender['id']}/pricing-findings", headers=headers).json() == []
    assert fake_client.calls == []

    await run_operation(body["operation_id"])

    operation = client.get(f"/operations/{body['operation_id']}", headers=headers).json()
    assert operation["status"] == "succeeded"
    findings = client.get(f"/tenders/{tender['id']}/pricing-findings", headers=headers).json()
    assert len(findings) == 4
    assert len(fake_client.calls) == 4

    # Re-validating once the first finished: a new operation, and no LLM
    # call, because every finding is already stored.
    again = client.post(url, headers=headers)
    assert again.status_code == 202, again.text
    assert again.json()["operation_id"] != body["operation_id"]
    await run_operation(again.json()["operation_id"])
    assert (
        client.get(f"/operations/{again.json()['operation_id']}", headers=headers).json()["status"]
        == "succeeded"
    )
    assert len(client.get(f"/tenders/{tender['id']}/pricing-findings", headers=headers).json()) == 4
    assert len(fake_client.calls) == 4


async def test_validate_pricing_document_404s_for_an_unknown_id(
    client: TestClient, register_actor
):
    headers, _, _org_id = register_actor()

    response = client.post(f"/pricing-documents/{uuid.uuid4()}/validate", headers=headers)

    assert response.status_code == 404, response.text


async def test_list_pricing_findings_returns_the_real_finding(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Commercial Route Test Bid 3"}, headers=headers
    ).json()
    finding_id = await _make_pricing_finding(tender["id"])

    response = client.get(f"/tenders/{tender['id']}/pricing-findings", headers=headers)

    assert response.status_code == 200, response.text
    assert finding_id in [f["id"] for f in response.json()]


async def test_resolve_pricing_finding_sets_resolved(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Commercial Route Test Bid 4"}, headers=headers
    ).json()
    finding_id = await _make_pricing_finding(tender["id"])

    response = client.post(f"/pricing-findings/{finding_id}/resolve", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["resolved"] is True


async def test_resolve_pricing_finding_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.post(f"/pricing-findings/{uuid.uuid4()}/resolve", headers=headers)

    assert response.status_code == 404, response.text


async def test_commercial_routes_reject_a_role_outside_resolve_commercial_finding(
    client: TestClient, register_actor, add_member_with_role
):
    # resolve_commercial_finding's own ACTION_ROLES row is {bid_manager,
    # commercial_reviewer, approver, admin} - talent_reviewer is a real
    # member but not in that set.
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Commercial Route Test Bid 5"},
        headers=admin_headers,
    ).json()
    finding_id = await _make_pricing_finding(tender["id"])

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "talent_reviewer")

    response = client.get(f"/tenders/{tender['id']}/pricing-findings", headers=member_headers)

    assert response.status_code == 403, response.text
    finding_response = client.post(
        f"/pricing-findings/{finding_id}/resolve", headers=member_headers
    )
    assert finding_response.status_code == 403, finding_response.text


async def test_commercial_routes_reject_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    # The role-breadth test above proves resolve_commercial_finding's own
    # 4-role set is enforced WITHIN one org - this proves org isolation: a
    # real Commercial reviewer (a genuinely permitted role) of a DIFFERENT
    # organization must still be rejected. All 4 of this module's routes
    # had zero cross-org coverage before this test - confirmed by grep,
    # not assumed, since three sibling modules (gates/graph/redteam) turned
    # out to already have partial coverage under a name this project's own
    # earlier search missed (singular "reject" vs "rejects").
    admin_headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Commercial Route Test Bid 6"},
        headers=admin_headers,
    ).json()
    pricing_document_id = await _ingest_pricing_sheet(
        org_id, tender["id"], "pricing_schedule_1.xlsx"
    )
    finding_id = await _make_pricing_finding(tender["id"])

    outsider_headers, _, _ = register_actor()

    list_docs = client.get(f"/tenders/{tender['id']}/pricing-documents", headers=outsider_headers)
    assert list_docs.status_code == 403, list_docs.text
    assert list_docs.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    validate = client.post(
        f"/pricing-documents/{pricing_document_id}/validate", headers=outsider_headers
    )
    assert validate.status_code == 403, validate.text
    assert validate.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    list_findings = client.get(
        f"/tenders/{tender['id']}/pricing-findings", headers=outsider_headers
    )
    assert list_findings.status_code == 403, list_findings.text
    assert list_findings.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    resolve = client.post(f"/pricing-findings/{finding_id}/resolve", headers=outsider_headers)
    assert resolve.status_code == 403, resolve.text
    assert resolve.json()["error"]["code"] == "ORG_ACCESS_DENIED"
