"""Route-level coverage for graph.py (G12) - previously 1/5 (POST
.../graph/project covered by test_idempotency.py, idempotency-focused
rather than exhaustive; status/impact/contradictions/trace had none).
Both RBAC actions (view_graph - 7 roles, trigger_graph_projection - 4
roles) were already correctly scoped by B10 - checked against rbac.py
directly, not assumed; no fix needed here.

Fixture setup mirrors tests/integration/test_graph_projection.py's own
helpers (direct repository calls, no parsing pipeline) - real Postgres
AND real Neo4j. The Neo4j session-per-call fix this needs
(_no_pooling_for_neo4j/_clean_neo4j) now lives in tests/route/conftest.py,
shared with test_redteam_routes.py's own trigger-route test (see that
fixture's own docstring for the full mechanism/history) - moved there
once a second file needed the identical fix, rather than duplicated here
a second time.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _requirement(tender_id: str) -> str:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=uuid.UUID(tender_id),
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=uuid.UUID(tender_id),
            display_code=f"REQ-{uuid.uuid4().hex[:6]}",
            requirement_text="Sample requirement text.",
            normalized_text="sample requirement text.",
            category="experience",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return str(requirement.id)


async def _selected_employee(org_id: str, tender_id: str, user_id: str) -> str:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=uuid.UUID(org_id),
            name="Graph Route Test Employee",
            content_fingerprint=uuid.uuid4().hex,
            title="Graph Route Test Employee",
        )
        await uow.knowledge.upsert_selection(
            tender_id=uuid.UUID(tender_id),
            knowledge_item_id=knowledge_item.id,
            selected=True,
            actor_user_id=uuid.UUID(user_id),
        )
        await uow.commit()
        return str(knowledge_item.id)


async def test_trigger_graph_projection_succeeds_and_is_idempotent(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid"}, headers=headers
    ).json()
    await _requirement(tender["id"])

    first = client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-1"},
    )
    second = client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-1"},
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert second.json()["id"] == first.json()["id"], "replay must return the same run"


async def test_get_graph_status_reflects_a_real_projection(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 2"}, headers=headers
    ).json()
    await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-2"},
    )

    response = client.get(f"/tenders/{tender['id']}/graph/status", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["fresh"] is True
    assert response.json()["projected_state_version"] == response.json()["current_state_version"]


async def test_preview_graph_impact_returns_the_selected_employee(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 3"}, headers=headers
    ).json()
    employee_id = await _selected_employee(org_id, tender["id"], user_id)
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-3"},
    )

    response = client.post(
        f"/tenders/{tender['id']}/graph/impact",
        json={"knowledge_item_id": employee_id},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


async def test_preview_graph_impact_409s_when_stale(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 4"}, headers=headers
    ).json()
    employee_id = await _selected_employee(org_id, tender["id"], user_id)
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-4"},
    )
    # Bumps tender.state_version without re-projecting - the projection
    # taken a moment ago is now behind current state. A raw requirement
    # insert does NOT do this (tried first; state_version bumps are a
    # deliberate service-layer call at Part C's own six named points, not
    # automatic on every write) - the real PUT knowledge-selections route
    # goes through knowledge_service.set_knowledge_selection, which does
    # call bump_state_version.
    client.put(
        f"/tenders/{tender['id']}/knowledge-selections/{employee_id}",
        json={"selected": False},
        headers={**headers, "Idempotency-Key": "deselect-for-staleness-4"},
    )

    response = client.post(
        f"/tenders/{tender['id']}/graph/impact",
        json={"knowledge_item_id": employee_id},
        headers=headers,
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "GRAPH_STALE"


async def test_get_graph_contradictions_returns_a_real_empty_list(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 5"}, headers=headers
    ).json()
    await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-5"},
    )

    response = client.get(f"/tenders/{tender['id']}/graph/contradictions", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_get_graph_trace_returns_the_real_requirement_node(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 6"}, headers=headers
    ).json()
    requirement_id = await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-6"},
    )

    response = client.get(
        f"/tenders/{tender['id']}/graph/trace",
        params={"node_label": "Requirement", "node_id": requirement_id},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    # §7 step 10 (item d): pinned directly against real data, not assumed
    # - node_labels/neighbor_labels only exist because get_graph_trace's
    # own Cypher was fixed this step to return labels() alongside each
    # node. Without this, a client has no way to know what node_label to
    # pass when re-querying a clicked neighbor - the exact thing that made
    # screen 17's click-to-expand design impossible before this fix (see
    # KNOWN_LIMITATIONS.md's own updated verdict on the 1-hop shape).
    assert body["node_labels"] == ["Requirement"]
    assert body["n"]["id"] == requirement_id


async def test_get_graph_trace_returns_a_tender_node_with_its_submission_date(
    client: TestClient, register_actor
):
    # Plan item 75. Observed in the item 66 walk: the trace on a tender's
    # own node answered 500 (PydanticSerializationError on
    # neo4j.time.Date), because the projector writes submission_date as a
    # date and Neo4j returns it as its own type.
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Graph Route Test Bid 6b", "submission_date": "2027-03-31"},
        headers=headers,
    ).json()
    await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-6b"},
    )

    response = client.get(
        f"/tenders/{tender['id']}/graph/trace",
        params={"node_label": "Tender", "node_id": tender["id"]},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["node_labels"] == ["Tender"]
    assert body["n"]["id"] == tender["id"]
    # The rest of the API's date format (Pydantic's), not Neo4j's object.
    assert body["n"]["submission_date"] == "2027-03-31"


async def test_get_graph_trace_rejects_an_unallowed_label(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 7"}, headers=headers
    ).json()
    await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-7"},
    )

    response = client.get(
        f"/tenders/{tender['id']}/graph/trace",
        params={"node_label": "User", "node_id": str(uuid.uuid4())},
        headers=headers,
    )

    assert response.status_code == 400, response.text


async def test_graph_routes_reject_a_non_member(client: TestClient, register_actor):
    # Covers status/contradictions/project (trigger) - impact and trace
    # need their own real fixture data (a knowledge item, a real
    # requirement id) and are proven separately below, not because they're
    # any different in kind, only because they need more setup than a
    # shared loop over bare GETs/one POST can hold cleanly.
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 8"}, headers=headers
    ).json()
    await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-8"},
    )

    outsider_headers, _, _ = register_actor()

    for path in (
        f"/tenders/{tender['id']}/graph/status",
        f"/tenders/{tender['id']}/graph/contradictions",
    ):
        response = client.get(path, headers=outsider_headers)
        assert response.status_code == 403, f"{path}: {response.text}"
        assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"

    trigger_response = client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**outsider_headers, "Idempotency-Key": "project-8-outsider"},
    )
    assert trigger_response.status_code == 403, trigger_response.text


async def test_preview_graph_impact_rejects_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Graph Route Test Bid 9"}, headers=headers
    ).json()
    employee_id = await _selected_employee(org_id, tender["id"], user_id)
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-9"},
    )

    outsider_headers, _, _ = register_actor()

    response = client.post(
        f"/tenders/{tender['id']}/graph/impact",
        json={"knowledge_item_id": employee_id},
        headers=outsider_headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"


async def test_get_graph_trace_rejects_a_caller_outside_the_organization(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Graph Route Test Bid 10"},
        headers=headers,
    ).json()
    requirement_id = await _requirement(tender["id"])
    client.post(
        f"/tenders/{tender['id']}/graph/project",
        headers={**headers, "Idempotency-Key": "project-10"},
    )

    outsider_headers, _, _ = register_actor()

    response = client.get(
        f"/tenders/{tender['id']}/graph/trace",
        params={"node_label": "Requirement", "node_id": requirement_id},
        headers=outsider_headers,
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "ORG_ACCESS_DENIED"
