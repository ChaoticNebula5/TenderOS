"""Plan item 111 (PR 24): the import-item routes for choosing the employee a
resume belongs to, or importing it as a new person - the listing's
candidates, approve with target_employee_id, POST .../import-as-new, and
each refusal's code. State is seeded through the repository, as
test_resume_review_routes.py does; no worker runs here. Every fixture value
is fake.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service

pytestmark = pytest.mark.asyncio

NAME = "Avery Lark"


async def _document(org_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    label = uuid.uuid4().hex
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=user_id,
        filename=f"resume_{label}.docx",
        content=f"placeholder:{label}".encode(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="resume",
    )
    async with UnitOfWork() as uow:
        await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="Lead Analyst, 9 years.",
            normalized_text="lead analyst, 9 years.",
            content_hash=uuid.uuid4().hex,
        )
        await uow.commit()
    return document.id


async def _seed(
    org: str,
    user: str,
    *,
    email: str | None = None,
    import_type: str = "bulk_resumes",
) -> tuple[str, str, list[str]]:
    """(batch_id, item_id, employee ids): two employees with one name and a
    resume item in review naming neither."""
    org_id, user_id = uuid.UUID(org), uuid.UUID(user)
    document_id = await _document(org_id, user_id)
    employees = []
    async with UnitOfWork() as uow:
        for title in ("Analyst", "Engineer"):
            item, _ = await uow.knowledge.create_employee(
                organization_id=org_id,
                name=NAME,
                content_fingerprint=uuid.uuid4().hex,
                title=NAME,
                current_title=title,
                email=email if title == "Analyst" else None,
            )
            employees.append(str(item.id))
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id, import_type=import_type, created_by=user_id
        )
        review = await uow.knowledge.create_import_item(
            batch_id=batch.id,
            source_ref=f"{document_id}/resume.docx",
            extracted_payload=ExtractedEmployeeProfile(
                name=NAME, email=email, current_title="Lead Analyst", confidence=0.8
            ).model_dump(),
            source_document_id=document_id,
        )
        await uow.knowledge.update_import_item(review.id, status="needs_review")
        await uow.commit()
    return str(batch.id), str(review.id), employees


def _code(response) -> str:
    return response.json()["error"]["code"]


def _row(client: TestClient, headers, batch_id: str) -> dict:
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    return row


def _pick_body(row: dict, employee_id: str) -> dict:
    shown = next(
        c for c in row["candidates"]["candidates"] if c["knowledge_item_id"] == employee_id
    )
    return {
        "target_fingerprint": shown["fingerprint"],
        "target_employee_id": employee_id,
        "candidates_fingerprint": row["candidates"]["fingerprint"],
    }


async def test_the_listing_carries_the_candidates_and_a_pick_merges_into_one(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, second) = await _seed(org_id, user_id)

    row = _row(client, headers, batch_id)
    assert row["target_employee"] is None
    candidates = row["candidates"]
    assert {c["knowledge_item_id"] for c in candidates["candidates"]} == {first, second}
    assert candidates["strong_match"] is False
    assert len(candidates["fingerprint"]) == 64
    assert all(c["signals"] == ["name"] for c in candidates["candidates"])
    assert all("held_by_other" in e for c in candidates["candidates"] for e in c["changes"])

    picked = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json=_pick_body(row, second),
        headers=headers,
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["target_knowledge_item_id"] == second
    employee = client.get(f"/employees/{second}", headers=headers).json()
    assert employee["current_title"] == "Lead Analyst"
    assert _row(client, headers, batch_id)["candidates"] is None


async def test_import_as_new_answers_200_with_a_new_employee(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, employees = await _seed(org_id, user_id)
    row = _row(client, headers, batch_id)

    response = client.post(
        f"/knowledge/import-items/{item_id}/import-as-new",
        json={"candidates_fingerprint": row["candidates"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    created = response.json()["target_knowledge_item_id"]
    assert created not in employees
    assert client.get(f"/employees/{created}", headers=headers).json()["name"] == NAME


async def test_a_changed_candidate_list_answers_409(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, _) = await _seed(org_id, user_id)
    row = _row(client, headers, batch_id)
    edited = client.patch(
        f"/employees/{first}/profile", json={"professional_location": "Goa"}, headers=headers
    )
    assert edited.status_code == 200, edited.text

    for path, body in (
        ("approve", _pick_body(row, first)),
        ("import-as-new", {"candidates_fingerprint": row["candidates"]["fingerprint"]}),
    ):
        response = client.post(
            f"/knowledge/import-items/{item_id}/{path}", json=body, headers=headers
        )
        assert response.status_code == 409, response.text
        assert _code(response) == "IMPORT_ITEM_CANDIDATES_CHANGED"


async def test_a_pick_of_an_employee_that_is_not_a_candidate_answers_409(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, _) = await _seed(org_id, user_id)
    row = _row(client, headers, batch_id)
    body = _pick_body(row, first) | {"target_employee_id": str(uuid.uuid4())}
    response = client.post(f"/knowledge/import-items/{item_id}/approve", json=body, headers=headers)
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_NOT_A_CANDIDATE"


async def test_import_as_new_on_a_strong_match_answers_409(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, _ = await _seed(org_id, user_id, email="avery@example.test")
    row = _row(client, headers, batch_id)
    assert row["candidates"]["strong_match"] is True
    response = client.post(
        f"/knowledge/import-items/{item_id}/import-as-new",
        json={"candidates_fingerprint": row["candidates"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_STRONG_MATCH"


async def test_a_pick_without_the_candidates_fingerprint_answers_422(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, _) = await _seed(org_id, user_id)
    row = _row(client, headers, batch_id)
    body = _pick_body(row, first) | {"candidates_fingerprint": None}
    response = client.post(f"/knowledge/import-items/{item_id}/approve", json=body, headers=headers)
    assert response.status_code == 422, response.text
    assert _code(response) == "IMPORT_ITEM_FINGERPRINT_REQUIRED"


async def test_a_second_decision_on_a_decided_item_answers_409(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, second) = await _seed(org_id, user_id)
    row = _row(client, headers, batch_id)
    assert (
        client.post(
            f"/knowledge/import-items/{item_id}/approve",
            json=_pick_body(row, first),
            headers=headers,
        ).status_code
        == 200
    )
    for path, body in (
        ("approve", _pick_body(row, second)),
        ("import-as-new", {"candidates_fingerprint": row["candidates"]["fingerprint"]}),
    ):
        response = client.post(
            f"/knowledge/import-items/{item_id}/{path}", json=body, headers=headers
        )
        assert response.status_code == 409, response.text
        assert _code(response) == "IMPORT_ITEM_NOT_IN_REVIEW"


async def test_other_import_types_answer_409_type_unsupported(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, (first, _) = await _seed(org_id, user_id, import_type="employee_master_xlsx")
    assert _row(client, headers, batch_id)["candidates"] is None
    for path, body in (
        (
            "approve",
            {"target_fingerprint": "x", "target_employee_id": first, "candidates_fingerprint": "y"},
        ),
        ("import-as-new", {"candidates_fingerprint": "y"}),
    ):
        response = client.post(
            f"/knowledge/import-items/{item_id}/{path}", json=body, headers=headers
        )
        assert response.status_code == 409, response.text
        assert _code(response) == "IMPORT_ITEM_TYPE_UNSUPPORTED"
