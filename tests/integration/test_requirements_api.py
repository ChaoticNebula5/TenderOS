"""G5's requirement-review routes over real HTTP - list/get/patch (confirm/
edit/reject)/verify/merge/split, plus cross-org isolation."""

import io
import uuid

import docx
import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.requirement_service import (
    RequirementDraft,
    persist_extraction_results,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _docx_bytes(text: str) -> bytes:
    document = docx.Document()
    document.add_paragraph(text)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def _make_tender_with_requirements(
    client: AsyncClient, email: str, org_name: str, texts: list[str]
) -> tuple[str, str, list[dict]]:
    """Registers a user/org/tender via HTTP, then seeds real requirement rows
    directly through the domain service (persist_extraction_results) rather
    than a live LLM call - the API surface under test is the review routes,
    not extraction itself."""
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = register.json()
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    tender_resp = await client.post(
        f"/organizations/{data['organization_id']}/tenders",
        json={"name": "Requirements API RFP"},
        headers=headers,
    )
    tender_id = tender_resp.json()["id"]

    upload_resp = await client.post(
        f"/tenders/{tender_id}/documents",
        files={"file": ("annexure.docx", _docx_bytes(" / ".join(texts)), _DOCX_TYPE)},
        headers=headers,
    )
    document_id = upload_resp.json()["id"]
    await parse_and_embed_document(document_id)

    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(uuid.UUID(document_id))
    chunk_id = chunks[0].id

    drafts = [
        RequirementDraft(
            requirement_text=text,
            category="other",
            mandatory=True,
            source_chunk_id=chunk_id,
            document_id=uuid.UUID(document_id),
            confidence=0.8,
        )
        for text in texts
    ]
    created = await persist_extraction_results(uuid.UUID(tender_id), drafts)
    return token, tender_id, [{"id": str(r.id), "text": r.requirement_text} for r in created]


async def test_list_and_get_requirements():
    async with await _client() as client:
        token, tender_id, requirements = await _make_tender_with_requirements(
            client, "req-api-1@example.com", "Req API Org 1", ["Bidders must submit form A."]
        )
        headers = {"Authorization": f"Bearer {token}"}

        listed = await client.get(f"/tenders/{tender_id}/requirements", headers=headers)
        assert listed.status_code == 200
        assert len(listed.json()) == 1

        got = await client.get(f"/requirements/{requirements[0]['id']}", headers=headers)
        assert got.status_code == 200
        assert got.json()["requirement_text"] == requirements[0]["text"]


async def test_patch_confirm_sets_human_reviewed():
    async with await _client() as client:
        token, _tender_id, requirements = await _make_tender_with_requirements(
            client, "req-api-2@example.com", "Req API Org 2", ["Bidders must submit form B."]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.patch(
            f"/requirements/{requirements[0]['id']}", json={}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["human_reviewed"] is True


async def test_patch_edit_changes_text_and_marks_reviewed():
    async with await _client() as client:
        token, _tender_id, requirements = await _make_tender_with_requirements(
            client, "req-api-3@example.com", "Req API Org 3", ["Bidders must submit form C."]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.patch(
            f"/requirements/{requirements[0]['id']}",
            json={"requirement_text": "Bidders must submit the corrected form C."},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["requirement_text"] == "Bidders must submit the corrected form C."
        assert resp.json()["human_reviewed"] is True


async def test_patch_reject_excludes_from_default_listing():
    async with await _client() as client:
        token, tender_id, requirements = await _make_tender_with_requirements(
            client, "req-api-4@example.com", "Req API Org 4", ["Bidders must submit form D."]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.patch(
            f"/requirements/{requirements[0]['id']}",
            json={"action": "reject", "notes": "not a real requirement"},
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["rejected_at"] is not None

        listed = await client.get(f"/tenders/{tender_id}/requirements", headers=headers)
        assert listed.json() == []


async def test_verify_returns_202_without_running_the_llm_inline():
    async with await _client() as client:
        token, _tender_id, requirements = await _make_tender_with_requirements(
            client, "req-api-5@example.com", "Req API Org 5", ["Bidders must submit form E."]
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            f"/requirements/{requirements[0]['id']}/verify", headers=headers
        )
        assert resp.status_code == 202
        # line 538: no LLM call inside the request - verified is still false,
        # the real work is left for the worker via the outbox.
        assert resp.json()["verified"] is False


async def test_merge_two_requirements_into_one():
    async with await _client() as client:
        token, _tender_id, requirements = await _make_tender_with_requirements(
            client,
            "req-api-6@example.com",
            "Req API Org 6",
            ["Bidders must submit form F1.", "Bidders must submit form F2."],
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            "/requirements/merge",
            json={
                "requirement_ids": [r["id"] for r in requirements],
                "merged_text": "Bidders must submit forms F1 and F2.",
                "category": "other",
                "mandatory": True,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert resp.json()["requirement_text"] == "Bidders must submit forms F1 and F2."

        for original in requirements:
            check = await client.get(f"/requirements/{original['id']}", headers=headers)
            assert check.status_code == 404


async def test_split_one_requirement_into_two():
    async with await _client() as client:
        token, _tender_id, requirements = await _make_tender_with_requirements(
            client,
            "req-api-7@example.com",
            "Req API Org 7",
            ["Bidders must submit form G and form H."],
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.post(
            f"/requirements/{requirements[0]['id']}/split",
            json={
                "new_items": [
                    {"requirement_text": "Bidders must submit form G."},
                    {"requirement_text": "Bidders must submit form H."},
                ]
            },
            headers=headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        check = await client.get(f"/requirements/{requirements[0]['id']}", headers=headers)
        assert check.status_code == 404


async def test_requirements_are_not_visible_across_organizations():
    async with await _client() as client:
        _owner_token, _tender_id, requirements = await _make_tender_with_requirements(
            client,
            "req-api-owner@example.com",
            "Req API Owner Org",
            ["Bidders must submit form I."],
        )

        outsider = await client.post(
            "/auth/register",
            json={
                "email": "req-api-outsider@example.com",
                "password": "hunter2",
                "organization_name": "Req API Outsider Org",
            },
        )
        outsider_token = outsider.json()["access_token"]

        blocked = await client.get(
            f"/requirements/{requirements[0]['id']}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert blocked.status_code == 403


async def test_requirements_routes_reject_a_caller_outside_the_organization():
    """B16 item 3: only GET /requirements/{id} (above) had real cross-org
    coverage - list/patch/verify/merge/split all had none. Same shape
    throughout: a real Admin of a DIFFERENT organization."""
    async with await _client() as client:
        owner_token, tender_id, requirements = await _make_tender_with_requirements(
            client,
            "req-api-owner2@example.com",
            "Req API Owner Org 2",
            ["Bidders must submit form J1.", "Bidders must submit form J2."],
        )

        outsider = await client.post(
            "/auth/register",
            json={
                "email": "req-api-outsider2@example.com",
                "password": "hunter2",
                "organization_name": "Req API Outsider Org 2",
            },
        )
        outsider_headers = {"Authorization": f"Bearer {outsider.json()['access_token']}"}

        list_resp = await client.get(
            f"/tenders/{tender_id}/requirements", headers=outsider_headers
        )
        assert list_resp.status_code == 403, list_resp.text

        patch_resp = await client.patch(
            f"/requirements/{requirements[0]['id']}", json={}, headers=outsider_headers
        )
        assert patch_resp.status_code == 403, patch_resp.text

        verify_resp = await client.post(
            f"/requirements/{requirements[0]['id']}/verify", headers=outsider_headers
        )
        assert verify_resp.status_code == 403, verify_resp.text

        merge_resp = await client.post(
            "/requirements/merge",
            json={
                "requirement_ids": [r["id"] for r in requirements],
                "merged_text": "Hijacked merge",
                "category": "other",
                "mandatory": True,
            },
            headers=outsider_headers,
        )
        assert merge_resp.status_code == 403, merge_resp.text

        split_resp = await client.post(
            f"/requirements/{requirements[0]['id']}/split",
            json={"new_items": [{"requirement_text": "Hijacked split A"}]},
            headers=outsider_headers,
        )
        assert split_resp.status_code == 403, split_resp.text


async def _make_tender_with_submission_item(
    client: AsyncClient, email: str, org_name: str
) -> tuple[str, str, str]:
    """Same shape as _make_tender_with_requirements above, but seeds a real
    submission_items row directly through the domain service - the write
    path under test (PATCH /submission-items/{id}) had zero callers
    anywhere before B16 (KNOWN_LIMITATIONS.md: "No screen manages
    submission-item status"), so there's no extraction flow to seed
    through instead."""
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = register.json()
    token = data["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    tender_resp = await client.post(
        f"/organizations/{data['organization_id']}/tenders",
        json={"name": "Submission Items API RFP"},
        headers=headers,
    )
    tender_id = tender_resp.json()["id"]

    async with UnitOfWork() as uow:
        item = await uow.requirements.create_submission_item(
            tender_id=uuid.UUID(tender_id),
            name="Tax clearance certificate",
            mandatory=True,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
    return token, tender_id, str(item.id)


async def test_list_submission_items():
    async with await _client() as client:
        token, tender_id, item_id = await _make_tender_with_submission_item(
            client, "sub-api-1@example.com", "Sub API Org 1"
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tenders/{tender_id}/submission-items", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == item_id
        assert body[0]["status"] == "missing"
        assert body[0]["mandatory"] is True


async def test_patch_submission_item_updates_status():
    async with await _client() as client:
        token, _tender_id, item_id = await _make_tender_with_submission_item(
            client, "sub-api-2@example.com", "Sub API Org 2"
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.patch(
            f"/submission-items/{item_id}", json={"status": "present"}, headers=headers
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "present"

        # Confirmed persisted, not just echoed back in the response - no
        # GET-by-id route exists for a single submission item (only list),
        # so re-fetch through the list endpoint instead.
        listing = await client.get(f"/tenders/{_tender_id}/submission-items", headers=headers)
        assert listing.json()[0]["status"] == "present"


async def test_submission_items_are_not_visible_across_organizations():
    """Direct regression for the item_id/submission_item_id path-param
    collision caught while building this route: dependencies.py's
    _resolve_organization_id already had an item_id branch bound to
    knowledge_import_items (B6) - reusing that name here would have
    resolved organization from the wrong table entirely, not just failed
    to isolate. This test would not reliably catch a return to that bug
    (a wrong-table lookup could coincidentally 404 too), but it does prove
    the route is org-isolated as implemented now.
    """
    async with await _client() as client:
        _owner_token, tender_id, item_id = await _make_tender_with_submission_item(
            client, "sub-api-owner@example.com", "Sub API Owner Org"
        )

        outsider = await client.post(
            "/auth/register",
            json={
                "email": "sub-api-outsider@example.com",
                "password": "hunter2",
                "organization_name": "Sub API Outsider Org",
            },
        )
        outsider_headers = {"Authorization": f"Bearer {outsider.json()['access_token']}"}

        blocked = await client.patch(
            f"/submission-items/{item_id}", json={"status": "present"}, headers=outsider_headers
        )
        assert blocked.status_code == 403

        # B16 item 3: list_submission_items itself had no cross-org
        # coverage - only the item-level PATCH above did.
        blocked_list = await client.get(
            f"/tenders/{tender_id}/submission-items", headers=outsider_headers
        )
        assert blocked_list.status_code == 403, blocked_list.text
