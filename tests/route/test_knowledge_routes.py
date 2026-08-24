"""Route-level coverage for knowledge.py (G6) - originally 18 routes with
zero HTTP-level coverage anywhere before this file, now 19: GET
/employees/{id} (screen 4's own gap - see KNOWN_LIMITATIONS.md) was added
alongside this file, not before it. This is the exact module
set_knowledge_selection's own dead-route TypeError bug lived in
undetected for several blocks (see KNOWN_LIMITATIONS.md and this
session's own git history) - the motivating example for building
full-module route coverage in the first place, not a module chosen at
random.

Upload routes (1-4) don't run the real import pipeline synchronously (no
live RQ worker in this test process, matching every other route test file
in this project) - they're tested for what they actually do: validate,
create a batch/document row, return 202. Import-item/employee state
needed by the read/review routes (5-17) is seeded directly through
knowledge_service/the repository layer, the same "call the real service
function, not a live worker" convention test_workflow_routes.py and
test_gates_routes.py already established for their own modules.

Every route in this module is gated on the single `manage_organization_
knowledge` action (routes/knowledge.py's own `_MANAGE` constant) - no
RBAC-widening bug here the way workflow.py/gates.py had, so this file
proves the boundary once (one route, narrower role rejected) rather than
repeating it 19 times.
"""

import io
import uuid

import docx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import knowledge_service

pytestmark = pytest.mark.asyncio


def _csv_bytes(text: str) -> bytes:
    return text.encode("utf-8")


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet["A1"] = "placeholder"
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Placeholder content for knowledge route test fixtures.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _seed_employee(org_id: str, name: str = "Dana Whitfield") -> str:
    async with UnitOfWork() as uow:
        knowledge_item, _employee = await uow.knowledge.create_employee(
            organization_id=uuid.UUID(org_id),
            name=name,
            content_fingerprint=uuid.uuid4().hex,
            title=name,
        )
        await uow.commit()
        return str(knowledge_item.id)


async def _seed_needs_review_item(org_id: str, created_by: str) -> tuple[str, str]:
    """A real import batch + a real import item sitting at needs_review -
    the state approve_import_item/reject_import_item actually act on.
    Returns (batch_id, item_id). created_by must be a real registered
    user's id - knowledge_import_batches.created_by is a real FK to
    users, not a free-standing UUID (first attempt at this helper used a
    bare uuid.uuid4() here and hit ForeignKeyViolationError on every
    caller - fixed by threading register_actor's own user_id through)."""
    batch = await knowledge_service.create_import_batch(
        uuid.UUID(org_id), "bulk_resumes", None, uuid.UUID(created_by)
    )
    async with UnitOfWork() as uow:
        item = await uow.knowledge.create_import_item(
            batch_id=batch.id, source_ref="row_1", extracted_payload={"name": "Test Person"}
        )
        await uow.knowledge.update_import_item(
            item.id, status="needs_review", error_message="ambiguous match"
        )
        await uow.commit()
    return str(batch.id), str(item.id)


# ----------------------------------------------------------------------
# 1-4: upload routes - validate, create a batch/document row, 202. No
# live worker in this process, so nothing downstream of queueing is
# exercised here (that's the service/worker layer's own test coverage).
# ----------------------------------------------------------------------


async def test_upload_employee_master_xlsx_creates_a_batch(client: TestClient, register_actor):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/employee-master",
        files={
            "file": (
                "employees.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )

    assert response.status_code == 202, response.text
    assert response.json()["import_type"] == "employee_master_xlsx"
    assert response.json()["organization_id"] == org_id


async def test_uploading_the_same_employee_master_file_twice_starts_a_second_batch(
    client: TestClient, register_actor
):
    # The broader shape of the bulk_resumes duplicate-file bug (see
    # KNOWN_LIMITATIONS.md): create_document's own org+hash dedup means a
    # second upload of byte-identical content reuses the same document.id.
    # Before the fix, queue_knowledge_document_import's idempotency key
    # varied by document_id alone, not batch_id, so this ordinary action
    # (re-importing the same spreadsheet to start a fresh batch) collided
    # with the first upload's outbox event and 500'd.
    headers, _, org_id = register_actor()
    file_kwargs = {
        "file": (
            "employees.xlsx",
            _xlsx_bytes(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }

    first = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/employee-master",
        files=file_kwargs,
        headers=headers,
    )
    second = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/employee-master",
        files=file_kwargs,
        headers=headers,
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["id"] != first.json()["id"], "must be a real second batch, not a replay"


async def test_upload_employee_master_csv_is_detected_by_filename(
    client: TestClient, register_actor
):
    # routes/knowledge.py's own import_type branch: ".csv" filename ->
    # employee_master_csv, not hardcoded xlsx - this is the B13 fix this
    # session made earlier; first route-level confirmation of it.
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/employee-master",
        files={"file": ("employees.csv", _csv_bytes("Name\r\nDana\r\n"), "text/csv")},
        headers=headers,
    )

    assert response.status_code == 202, response.text
    assert response.json()["import_type"] == "employee_master_csv"


async def test_upload_employee_master_rejects_an_unsupported_extension(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/employee-master",
        files={"file": ("employees.txt", b"not a real file", "text/plain")},
        headers=headers,
    )

    assert response.status_code == 415, response.text


async def test_upload_certifications_creates_a_batch(client: TestClient, register_actor):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/certifications",
        files={
            "file": (
                "certs.xlsx",
                _xlsx_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        headers=headers,
    )

    assert response.status_code == 202, response.text
    assert response.json()["import_type"] == "certification_documents"


async def test_upload_resumes_creates_one_batch_for_multiple_files(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/import-batches/resumes",
        files=[
            ("files", ("resume1.docx", _docx_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ("files", ("resume2.docx", _docx_bytes(),
                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
        ],
        headers=headers,
    )

    assert response.status_code == 202, response.text
    assert response.json()["import_type"] == "bulk_resumes"


async def test_upload_knowledge_document_returns_202_with_no_body(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()

    response = client.post(
        f"/organizations/{org_id}/knowledge/documents",
        files={"file": ("capabilities.docx", _docx_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=headers,
    )

    assert response.status_code == 202, response.text


# ----------------------------------------------------------------------
# 5-9: import batch/item listing and review
# ----------------------------------------------------------------------


async def test_list_import_batches_returns_the_real_batch(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, _item_id = await _seed_needs_review_item(org_id, user_id)

    response = client.get(f"/organizations/{org_id}/knowledge/import-batches", headers=headers)

    assert response.status_code == 200, response.text
    ids = [b["id"] for b in response.json()]
    assert batch_id in ids


async def test_get_import_batch_computes_needs_review_status(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    batch_id, _item_id = await _seed_needs_review_item(org_id, user_id)

    response = client.get(f"/knowledge/import-batches/{batch_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "needs_review"
    assert response.json()["total_items"] == 1


async def test_get_import_batch_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/knowledge/import-batches/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_list_import_items_returns_the_real_item(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    batch_id, item_id = await _seed_needs_review_item(org_id, user_id)

    response = client.get(f"/knowledge/import-batches/{batch_id}/items", headers=headers)

    assert response.status_code == 200, response.text
    ids = [i["id"] for i in response.json()]
    assert item_id in ids
    assert response.json()[0]["extracted_payload"] == {"name": "Test Person"}


async def test_approve_import_item_sets_status_approved(client: TestClient, register_actor):
    headers, user_id, org_id = register_actor()
    _batch_id, item_id = await _seed_needs_review_item(org_id, user_id)

    response = client.post(f"/knowledge/import-items/{item_id}/approve", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"


async def test_reject_import_item_sets_status_rejected_with_reason(
    client: TestClient, register_actor
):
    headers, user_id, org_id = register_actor()
    _batch_id, item_id = await _seed_needs_review_item(org_id, user_id)

    response = client.post(
        f"/knowledge/import-items/{item_id}/reject",
        json={"reason": "wrong person"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"


# ----------------------------------------------------------------------
# 10-16: knowledge item / employee query and correction
# ----------------------------------------------------------------------


async def test_list_organization_knowledge_returns_the_seeded_employee(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.get(f"/organizations/{org_id}/knowledge", headers=headers)

    assert response.status_code == 200, response.text
    assert item_id in [i["id"] for i in response.json()]


async def test_get_employee_returns_the_full_profile_even_when_unverified(
    client: TestClient, register_actor
):
    # The route this screen-4 gap analysis was about: an unverified
    # profile (never appears in list_bid_eligible_employees) must still
    # be fetchable by id, with real employee fields, not just the
    # generic knowledge-item shape GET /knowledge/{id} returns.
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id, name="Unverified Person")

    response = client.get(f"/employees/{item_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["knowledge_item_id"] == item_id
    assert response.json()["name"] == "Unverified Person"
    assert response.json()["profile_review_status"] != "verified"


async def test_get_employee_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/employees/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_list_bid_eligible_employees_excludes_unverified_profiles(
    client: TestClient, register_actor
):
    # B6's own invariant: unreviewed profiles never appear here, regardless
    # of how recently they were imported.
    headers, _, org_id = register_actor()
    await _seed_employee(org_id)

    response = client.get(f"/organizations/{org_id}/employees", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_get_knowledge_item_returns_the_real_item(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.get(f"/knowledge/{item_id}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["id"] == item_id


async def test_get_knowledge_item_404s_for_an_unknown_id(client: TestClient, register_actor):
    headers, _, _org_id = register_actor()

    response = client.get(f"/knowledge/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404, response.text


async def test_patch_knowledge_item_updates_title(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.patch(
        f"/knowledge/{item_id}", json={"title": "Renamed"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "Renamed"


async def test_patch_employee_profile_updates_named_fields(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.patch(
        f"/employees/{item_id}/profile",
        json={"current_title": "Principal Engineer"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["current_title"] == "Principal Engineer"
    # Correcting a field must not silently mark the profile reviewed - only
    # verify_employee_profile (the explicit approval action) may do that.
    assert response.json()["profile_review_status"] != "verified"


async def test_verify_employee_sets_eligibility_and_review_status(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.post(
        f"/employees/{item_id}/verify", json={"eligible_for_bid": True}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["eligible_for_bid"] is True
    assert response.json()["profile_review_status"] == "verified"


async def test_replace_employee_resume_creates_a_new_document_version(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.post(
        f"/employees/{item_id}/resume",
        files={"file": ("resume.docx", _docx_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=headers,
    )

    assert response.status_code == 202, response.text
    assert response.json()["resume_document_id"] is not None


# ----------------------------------------------------------------------
# 17-18: tender knowledge selections - the module's own dead-route bug
# lived in set_knowledge_selection specifically. Real HTTP coverage,
# not just the existing service-level tests in test_knowledge_service.py.
# ----------------------------------------------------------------------


async def test_list_knowledge_selections_returns_empty_before_any_selection(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Selections Test Bid"}, headers=headers
    ).json()

    response = client.get(f"/tenders/{tender['id']}/knowledge-selections", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == []


async def test_set_knowledge_selection_persists_and_is_listed(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Selections Test Bid 2"}, headers=headers
    ).json()
    item_id = await _seed_employee(org_id, name="Selected Person")

    put_response = client.put(
        f"/tenders/{tender['id']}/knowledge-selections/{item_id}",
        json={"selected": True, "change_reason": "matches required role"},
        headers={**headers, "Idempotency-Key": "select-1"},
    )

    assert put_response.status_code == 200, put_response.text
    assert put_response.json()["selected"] is True

    list_response = client.get(f"/tenders/{tender['id']}/knowledge-selections", headers=headers)
    assert item_id in [s["knowledge_item_id"] for s in list_response.json()]


async def test_set_knowledge_selection_requires_idempotency_key(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Selections Test Bid 2b"}, headers=headers
    ).json()
    item_id = await _seed_employee(org_id, name="No Key Person")

    response = client.put(
        f"/tenders/{tender['id']}/knowledge-selections/{item_id}",
        json={"selected": True},
        headers=headers,
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


async def test_set_knowledge_selection_rejects_a_knowledge_item_from_another_organization(
    client: TestClient, register_actor
):
    # The exact shape set_knowledge_selection's own cross-tenant fix
    # closed (this session's git history) - now proven through the real
    # HTTP route, not just the service function directly.
    headers, _, org_id = register_actor()
    tender = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Cross-Org Bid"}, headers=headers
    ).json()

    _other_headers, _, other_org_id = register_actor()
    foreign_item_id = await _seed_employee(other_org_id, name="Foreign Employee")

    response = client.put(
        f"/tenders/{tender['id']}/knowledge-selections/{foreign_item_id}",
        json={"selected": True},
        headers={**headers, "Idempotency-Key": "select-2"},
    )

    assert response.status_code == 404, response.text


async def test_set_knowledge_selection_404s_for_an_unknown_tender(
    client: TestClient, register_actor
):
    headers, _, org_id = register_actor()
    item_id = await _seed_employee(org_id)

    response = client.put(
        f"/tenders/{uuid.uuid4()}/knowledge-selections/{item_id}",
        json={"selected": True},
        headers={**headers, "Idempotency-Key": "select-3"},
    )

    assert response.status_code == 404, response.text


# ----------------------------------------------------------------------
# RBAC boundary - proven once, not per-route (every route in this module
# shares the same manage_organization_knowledge action).
# ----------------------------------------------------------------------


async def test_knowledge_routes_reject_a_role_outside_manage_organization_knowledge(
    client: TestClient, register_actor, add_member_with_role
):
    # manage_organization_knowledge's own ACTION_ROLES row (rbac.py) is
    # {bid_manager, solution_architect, talent_reviewer, admin} -
    # commercial_reviewer is a real member but not in that set.
    admin_headers, _, org_id = register_actor()
    member_email = f"{uuid.uuid4()}@example.com"
    member_headers, _, _ = register_actor(email=member_email)
    add_member_with_role(admin_headers, org_id, member_email, "commercial_reviewer")

    response = client.get(f"/organizations/{org_id}/knowledge", headers=member_headers)

    assert response.status_code == 403, response.text
