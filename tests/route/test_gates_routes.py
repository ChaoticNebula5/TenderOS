"""Route-level coverage for gates.py (G4) - list_gates/get_gate had zero
HTTP-level coverage anywhere (only POST /gates/{id}/decision is covered,
in tests/integration/test_workflow_api.py's
test_gate_decision_via_http_uses_the_gate_types_own_rbac_action). Both GET
routes were also found gated on create_bid while building this file - the
same "read action gated on a narrow authoring action" shape already fixed
three times this block (view_graph B10, view_readiness B12, view_workflow
B13) - the fourth instance, retrofitted to the new view_gates action here.
"""

import io
import uuid

import docx
import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.orchestration.nodes import requirement_extraction
from tenderos_worker.jobs.ingestion import parse_and_embed_document
from tenderos_worker.jobs.workflow import run_workflow

pytestmark = pytest.mark.asyncio

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Placeholder content for gate route test fixtures.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _tender_with_open_requirement_gate(
    client: TestClient, headers: dict, org_id: str, monkeypatch
) -> tuple[str, str]:
    """A real tender, a real uploaded+parsed document, and the real
    compiled LangGraph driven to its first pause (requirement_review) via
    run_workflow - not a hand-built gate row. requirement_extraction's own
    LLM call is faked (empty extraction) so this stays deterministic and
    free, matching test_workflow_api.py's own established pattern for the
    identical problem. Unlike test_orchestration_nodes.py's gate-shape
    pins (which call gate node functions directly, interrupt() monkey-
    patched), this goes through the real compiled graph and a real,
    unpatched interrupt() - the stronger of the two kinds of evidence this
    project's own KNOWN_LIMITATIONS discussion distinguishes, used here
    because a route test's whole point is exercising what a real request
    actually reaches.
    """
    fake_client = FakeLLMClient()
    fake_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [ExtractionOutput(requirements=[], submission_items=[], clarifications=[])],
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)

    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Gate Route Test Bid"}, headers=headers
    ).json()
    upload = client.post(
        f"/tenders/{tender['id']}/documents",
        files={"file": ("rfp.docx", _docx_bytes(), _DOCX_TYPE)},
        headers=headers,
    )
    await parse_and_embed_document(upload.json()["id"])
    run = client.post(
        f"/tenders/{tender['id']}/workflow-runs",
        headers={**headers, "Idempotency-Key": "gate-route-run"},
    ).json()

    await run_workflow(run["id"])
    return tender["id"], run["id"]


async def test_list_gates_returns_the_real_open_gate(
    client: TestClient, register_actor, monkeypatch
):
    headers, _, org_id = register_actor()
    _tender_id, run_id = await _tender_with_open_requirement_gate(
        client, headers, org_id, monkeypatch
    )

    response = client.get(f"/workflow-runs/{run_id}/gates", headers=headers)

    assert response.status_code == 200, response.text
    gates = response.json()
    assert len(gates) == 1
    assert gates[0]["gate_type"] == "requirement_review"
    assert gates[0]["status"] == "pending"
    assert set(gates[0]["summary"].keys()) == {
        "requirement_count",
        "mandatory_count",
        "unverified_mandatory_count",
        "submission_item_count",
        "clarification_count",
    }


async def test_get_gate_returns_the_real_gate(client: TestClient, register_actor, monkeypatch):
    headers, _, org_id = register_actor()
    _tender_id, run_id = await _tender_with_open_requirement_gate(
        client, headers, org_id, monkeypatch
    )
    gate_id = client.get(f"/workflow-runs/{run_id}/gates", headers=headers).json()[0]["gate_id"]

    response = client.get(f"/gates/{gate_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["gate_id"] == gate_id
    assert response.json()["workflow_run_id"] == run_id


async def test_get_gate_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/gates/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_gate_routes_are_readable_by_every_role_that_is_a_real_member(
    client: TestClient, register_actor, add_member_with_role, monkeypatch
):
    # view_gates's own point: a Commercial reviewer - previously locked out
    # by create_bid - must succeed reading a gate that has nothing to do
    # with their own specialty (this one's a requirement_review gate).
    admin_headers, _, org_id = register_actor()
    _tender_id, run_id = await _tender_with_open_requirement_gate(
        client, admin_headers, org_id, monkeypatch
    )
    gate_id = client.get(f"/workflow-runs/{run_id}/gates", headers=admin_headers).json()[0][
        "gate_id"
    ]

    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "commercial_reviewer")

    for path in (f"/workflow-runs/{run_id}/gates", f"/gates/{gate_id}"):
        response = client.get(path, headers=member_headers)
        assert response.status_code == 200, f"{path}: {response.text}"


async def test_gate_routes_reject_a_non_member(client: TestClient, register_actor, monkeypatch):
    # outsider_headers below is a real Admin (a permitted view_gates role)
    # of a SEPARATE organization, not an unauthenticated stranger -
    # register_actor always creates a full org+ADMIN membership. This is
    # already the cross-org proof (org-A member -> 403 on org-B's gate),
    # same shape as view_workflow's own equivalent test.
    admin_headers, _, org_id = register_actor()
    _tender_id, run_id = await _tender_with_open_requirement_gate(
        client, admin_headers, org_id, monkeypatch
    )
    gate_id = client.get(f"/workflow-runs/{run_id}/gates", headers=admin_headers).json()[0][
        "gate_id"
    ]

    outsider_headers, _, _ = register_actor()

    for path in (f"/workflow-runs/{run_id}/gates", f"/gates/{gate_id}"):
        response = client.get(path, headers=outsider_headers)
        assert response.status_code == 403, f"{path}: {response.text}"
        assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
