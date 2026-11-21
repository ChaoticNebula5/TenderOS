"""Plan item 112 (PR 25): the import-item routes for employee-master and
certification review items - the listing's master_target and
master_candidates, GET .../import-batches/{id}/holders, approve with a
fingerprint (and a chosen holder), import as new for a master row, and each
refusal's code. Rows are imported through the service's own import functions;
no worker runs here. Every fixture value is fake.
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.agents.knowledge.certifications_xlsx import CertificationRow
from tenderos_backend.agents.knowledge.employee_master import EmployeeMasterRow
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, knowledge_service

pytestmark = pytest.mark.asyncio

NAME = "Sol Brannick"


async def _employee(org_id: str, *, name: str = NAME, code: str | None = None, archived=False):
    async with UnitOfWork() as uow:
        item, _ = await uow.knowledge.create_employee(
            organization_id=uuid.UUID(org_id),
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
            employee_code=code,
            current_title="Analyst",
        )
        if archived:
            await uow.knowledge.update_knowledge_item(item.id, status="archived")
        await uow.commit()
    return str(item.id)


async def _master_item(org_id: str, user_id: str, row: EmployeeMasterRow) -> tuple[str, str]:
    batch = await knowledge_service.create_import_batch(
        uuid.UUID(org_id), "employee_master_xlsx", None, uuid.UUID(user_id)
    )
    item = await knowledge_service.import_employee_master_row(uuid.UUID(org_id), batch.id, row)
    return str(batch.id), str(item.id)


async def _cert_item(org_id: str, user_id: str, ref: str | None) -> tuple[str, str]:
    label = uuid.uuid4().hex
    document, _ = await document_service.create_document(
        organization_id=uuid.UUID(org_id),
        actor_user_id=uuid.UUID(user_id),
        filename=f"certifications_{label}.xlsx",
        content=f"placeholder:{label}".encode(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        side="company",
        doc_type="certification_documents",
    )
    batch = await knowledge_service.create_import_batch(
        uuid.UUID(org_id), "certification_documents", document.id, uuid.UUID(user_id)
    )
    row = CertificationRow(
        row_number=2,
        cert_key="C-1",
        employee_ref=ref,
        name="PRINCE2",
        issuer="Axelos",
        issue_date=date(2025, 3, 1),
        expiry_date=date(2028, 3, 1),
    )
    item = await knowledge_service.import_certification_row(
        uuid.UUID(org_id), batch.id, document.id, row, date(2026, 11, 20)
    )
    return str(batch.id), str(item.id)


def _row(code: str | None = "EMP-1", name: str = NAME) -> EmployeeMasterRow:
    return EmployeeMasterRow(
        row_number=2, employee_code=code, name=name, role="Lead Analyst", status=None
    )


def _code(response) -> str:
    return response.json()["error"]["code"]


def _item_row(client: TestClient, headers, batch_id: str) -> dict:
    [row] = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers).json()
    return row


async def test_a_master_item_lists_its_target_and_approval_applies_the_row(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    employee_id = await _employee(org_id)
    batch_id, item_id = await _master_item(org_id, user_id, _row("EMP-1"))

    row = _item_row(client, headers, batch_id)
    target = row["master_target"]
    assert target["knowledge_item_id"] == employee_id and target["archived"] is False
    assert [c["field"] for c in target["changes"]] == [
        "name",
        "employee_code",
        "current_title",
        "status",
    ]
    assert row["target_employee"] is None and row["candidates"] is None
    assert [c["knowledge_item_id"] for c in row["master_candidates"]["candidates"]] == [employee_id]

    refused = client.post(f"/knowledge/import-items/{item_id}/approve", headers=headers)
    assert refused.status_code == 422 and _code(refused) == "IMPORT_ITEM_FINGERPRINT_REQUIRED"
    approved = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": target["fingerprint"]},
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    employee = client.get(f"/employees/{employee_id}", headers=headers).json()
    assert (employee["employee_code"], employee["current_title"]) == ("EMP-1", "Lead Analyst")
    assert _item_row(client, headers, batch_id)["master_candidates"] is None


async def test_a_master_item_naming_no_one_takes_a_pick_or_import_as_new(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    await _employee(org_id)
    second = await _employee(org_id)
    batch_id, item_id = await _master_item(org_id, user_id, _row("EMP-2"))
    row = _item_row(client, headers, batch_id)
    assert row["master_target"] is None
    listed = row["master_candidates"]
    assert listed["strong_match"] is False and len(listed["candidates"]) == 2

    no_target = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={"target_fingerprint": "x"},
        headers=headers,
    )
    assert no_target.status_code == 409 and _code(no_target) == "IMPORT_ITEM_NO_TARGET"
    shown = next(c for c in listed["candidates"] if c["knowledge_item_id"] == second)
    picked = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={
            "target_fingerprint": shown["fingerprint"],
            "target_employee_id": second,
            "candidates_fingerprint": listed["fingerprint"],
        },
        headers=headers,
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["target_knowledge_item_id"] == second

    batch_id, item_id = await _master_item(org_id, user_id, _row("EMP-3"))
    listed = _item_row(client, headers, batch_id)["master_candidates"]
    made = client.post(
        f"/knowledge/import-items/{item_id}/import-as-new",
        json={"candidates_fingerprint": listed["fingerprint"]},
        headers=headers,
    )
    assert made.status_code == 200, made.text
    assert made.json()["target_knowledge_item_id"] not in {second}


async def test_master_import_as_new_answers_409_strong_match_on_a_code_match(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    await _employee(org_id, name="Sol Brannock", code="EMP-4")
    batch_id, item_id = await _master_item(org_id, user_id, _row("EMP-4"))
    listed = _item_row(client, headers, batch_id)["master_candidates"]
    assert listed["strong_match"] is True
    response = client.post(
        f"/knowledge/import-items/{item_id}/import-as-new",
        json={"candidates_fingerprint": listed["fingerprint"]},
        headers=headers,
    )
    assert response.status_code == 409 and _code(response) == "IMPORT_ITEM_STRONG_MATCH"


async def test_the_holders_route_lists_live_employees_and_approval_records_the_certificate(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    holder_id = await _employee(org_id)
    archived_id = await _employee(org_id, name="Gone Person", archived=True)
    batch_id, item_id = await _cert_item(org_id, user_id, "EMP-9")

    holders = client.get(f"/knowledge/import-batches/{batch_id}/holders", headers=headers)
    assert holders.status_code == 200, holders.text
    by_id = {h["knowledge_item_id"]: h for h in holders.json()}
    assert holder_id in by_id and archived_id not in by_id
    assert set(by_id[holder_id]) == {
        "knowledge_item_id",
        "name",
        "employee_code",
        "email",
        "current_title",
        "fingerprint",
    }

    approved = client.post(
        f"/knowledge/import-items/{item_id}/approve",
        json={
            "target_fingerprint": by_id[holder_id]["fingerprint"],
            "target_employee_id": holder_id,
        },
        headers=headers,
    )
    assert approved.status_code == 200, approved.text
    certificate_id = approved.json()["target_knowledge_item_id"]
    assert certificate_id is not None
    employee = client.get(f"/employees/{holder_id}", headers=headers).json()
    assert employee["employee_code"] == "EMP-9"


async def test_the_holders_route_refuses_a_member_of_another_organization(
    client: TestClient, register_actor
):
    """The roster is the batch's organization's: require_permission resolves
    the organization from the batch in the path and refuses a caller who is
    not a member of it, before the route reads anything."""
    _, user_id, org_id = register_actor()
    await _employee(org_id)
    batch_id, _ = await _cert_item(org_id, user_id, "EMP-8")
    other_headers, _, _ = register_actor()
    response = client.get(f"/knowledge/import-batches/{batch_id}/holders", headers=other_headers)
    assert response.status_code == 403 and _code(response) == "ORG_ACCESS_DENIED"
    assert NAME not in response.text


async def test_the_holders_route_answers_404_for_no_batch_and_empty_for_other_types(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    missing = client.get(f"/knowledge/import-batches/{uuid.uuid4()}/holders", headers=headers)
    assert missing.status_code == 404
    batch_id, _ = await _master_item(org_id, user_id, _row("EMP-5"))
    other = client.get(f"/knowledge/import-batches/{batch_id}/holders", headers=headers)
    assert other.status_code == 200 and other.json() == []


async def test_a_certificate_approval_answers_each_refusal_with_its_code(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    holder_id = await _employee(org_id)
    archived_id = await _employee(org_id, name="Gone Person", archived=True)
    batch_id, item_id = await _cert_item(org_id, user_id, None)
    _, _, other_org = register_actor()
    outsider = await _employee(other_org)
    [holder] = [
        h
        for h in client.get(f"/knowledge/import-batches/{batch_id}/holders", headers=headers).json()
        if h["knowledge_item_id"] == holder_id
    ]
    url = f"/knowledge/import-items/{item_id}/approve"
    cases = [
        ({"target_fingerprint": holder["fingerprint"]}, 409, "IMPORT_ITEM_NO_TARGET"),
        ({"target_employee_id": holder_id}, 422, "IMPORT_ITEM_FINGERPRINT_REQUIRED"),
        (
            {"target_fingerprint": "x", "target_employee_id": outsider},
            409,
            "IMPORT_ITEM_NOT_A_CANDIDATE",
        ),
        ({"target_fingerprint": "x", "target_employee_id": archived_id}, 409, "EMPLOYEE_ARCHIVED"),
        (
            {"target_fingerprint": "stale", "target_employee_id": holder_id},
            409,
            "IMPORT_ITEM_TARGET_CHANGED",
        ),
    ]
    for body, status_code, code in cases:
        response = client.post(url, json=body, headers=headers)
        assert (response.status_code, _code(response)) == (status_code, code), response.text
    as_new = client.post(
        f"/knowledge/import-items/{item_id}/import-as-new",
        json={"candidates_fingerprint": "x"},
        headers=headers,
    )
    assert as_new.status_code == 409 and _code(as_new) == "IMPORT_ITEM_TYPE_UNSUPPORTED"
