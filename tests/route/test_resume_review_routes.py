"""Plan item 80 (PR 18): the import-item routes for a resume review item - the
listing's target_employee and its fingerprint, approve with that
fingerprint, and each refusal's code. State is seeded through the
repository, as test_knowledge_routes.py does; no worker runs here. Every
fixture value is fake.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.schemas.knowledge import ExtractedEmployeeProfile
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, knowledge_service

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


async def _seed(org: str, user: str, *, with_target: bool = True) -> tuple[str, str, str | None]:
    """(batch_id, item_id, employee_id): a verified employee and a resume
    item in review naming them (or no one)."""
    org_id, user_id = uuid.UUID(org), uuid.UUID(user)
    first = await _document(org_id, user_id)
    second = await _document(org_id, user_id)
    employee_id: uuid.UUID | None = None
    async with UnitOfWork() as uow:
        if with_target:
            item, _ = await uow.knowledge.create_employee(
                organization_id=org_id,
                name=NAME,
                content_fingerprint=uuid.uuid4().hex,
                title=NAME,
                current_title="Analyst",
                resume_document_id=first,
                source_document_id=first,
            )
            await uow.knowledge.update_employee(
                item.id, profile_review_status="verified", eligible_for_bid=True
            )
            employee_id = item.id
        batch = await uow.knowledge.create_import_batch(
            organization_id=org_id, import_type="bulk_resumes", created_by=user_id
        )
        review = await uow.knowledge.create_import_item(
            batch_id=batch.id,
            source_ref=f"{second}/resume.docx",
            extracted_payload=ExtractedEmployeeProfile(
                name=NAME, current_title="Lead Analyst", years_experience=9, confidence=0.8
            ).model_dump(),
            source_document_id=second,
        )
        await uow.knowledge.update_import_item(
            review.id, status="needs_review", target_knowledge_item_id=employee_id
        )
        await uow.commit()
    return str(batch.id), str(review.id), str(employee_id) if employee_id else None


def _code(response) -> str:
    return response.json()["error"]["code"]


async def test_the_listing_carries_the_target_employee_and_approve_merges_with_it(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, employee_id = await _seed(org_id, user_id)

    listed = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers)
    assert listed.status_code == 200, listed.text
    [row] = listed.json()
    target = row["target_employee"]
    assert target["knowledge_item_id"] == employee_id
    assert target["name"] == NAME
    assert target["current_title"] == "Analyst"
    assert target["profile_review_status"] == "verified"
    assert target["merged_into_existing"] is False
    assert len(target["fingerprint"]) == 64

    approved = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": target["fingerprint"]},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    employee = client.get(f"/employees/{employee_id}", headers=headers).json()
    assert employee["current_title"] == "Lead Analyst"
    assert employee["profile_review_status"] == "verified"
    [after] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    assert after["target_employee"]["merged_into_existing"] is True


async def test_a_changed_employee_answers_409_target_changed(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, employee_id = await _seed(org_id, user_id)
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    edited = client.patch(
        f"/employees/{employee_id}/profile", json={"professional_location": "Goa"}, headers=headers
    )
    assert edited.status_code == 200, edited.text

    response = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": row["target_employee"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_TARGET_CHANGED"


async def test_a_resume_approval_without_the_fingerprint_answers_422(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    _batch_id, item_id, _employee_id = await _seed(org_id, user_id)
    for body in (None, {}):
        response = client.post(
            f"/knowledge/import-items/{item_id}/approve", json=body, headers=headers
        )
        assert response.status_code == 422, response.text
        assert _code(response) == "IMPORT_ITEM_FINGERPRINT_REQUIRED"


async def test_an_item_naming_no_employee_answers_409_no_target(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, _ = await _seed(org_id, user_id, with_target=False)
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    assert row["target_employee"] is None
    response = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": "x"},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_NO_TARGET"


async def test_an_item_with_no_document_answers_409_source_missing(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, _ = await _seed(org_id, user_id)
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(uuid.UUID(item_id), source_document_id=None)
        await uow.commit()
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    response = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": row["target_employee"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_SOURCE_MISSING"


async def test_an_archived_target_answers_409_employee_archived(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, employee_id = await _seed(org_id, user_id)
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    async with UnitOfWork() as uow:
        await uow.knowledge.update_knowledge_item(uuid.UUID(employee_id), status="archived")
        await uow.commit()
    response = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": row["target_employee"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "EMPLOYEE_ARCHIVED"


async def test_a_decided_item_answers_409_not_in_review_and_a_replay_200(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    _batch_id, item_id, _ = await _seed(org_id, user_id)
    rejected = client.post(
        f"/knowledge/import-items/{item_id}/reject", json={"reason": "other"}, headers=headers
    )
    assert rejected.status_code == 200, rejected.text
    again = client.post(
        f"/knowledge/import-items/{item_id}/reject", json={"reason": "other"}, headers=headers
    )
    assert again.status_code == 200 and again.json()["status"] == "rejected"
    approve = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": "x"},
        headers=headers,
    )
    assert approve.status_code == 409, approve.text
    assert _code(approve) == "IMPORT_ITEM_NOT_IN_REVIEW"
    assert "rejected" in approve.json()["error"]["message"]
    # The service's own replay rule, through the route, for an approval.
    _b, approved_id, employee_id = await _seed(org_id, user_id)
    async with UnitOfWork() as uow:
        employee = await uow.knowledge.get_employee(uuid.UUID(employee_id))
    assert employee is not None
    fingerprint = knowledge_service.resume_target_fingerprint(employee)
    for _ in range(2):
        response = client.post(
            f"/knowledge/import-items/{approved_id}/approve",
            json={"target_fingerprint": fingerprint},
            headers=headers,
        )
        assert response.status_code == 200, response.text


async def test_the_listing_carries_the_change_list_and_a_broken_payload_answers_409(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, item_id, _ = await _seed(org_id, user_id)
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    assert row["target_employee"]["changes"] == [
        {"field": "email", "on_file": None, "from_resume": None, "changes": False},
        {
            "field": "current_title",
            "on_file": "Analyst",
            "from_resume": "Lead Analyst",
            "changes": True,
        },
        {"field": "professional_location", "on_file": None, "from_resume": None, "changes": False},
        {"field": "years_experience", "on_file": None, "from_resume": 9.0, "changes": True},
    ]
    async with UnitOfWork() as uow:
        await uow.knowledge.update_import_item(uuid.UUID(item_id), extracted_payload={"name": NAME})
        await uow.commit()
    listed = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers)
    assert listed.status_code == 200, listed.text
    [broken] = listed.json()
    assert broken["target_employee"]["changes"] is None
    response = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": broken["target_employee"]["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409, response.text
    assert _code(response) == "IMPORT_ITEM_PAYLOAD_INVALID"
