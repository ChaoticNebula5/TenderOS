"""G2's document endpoints over real HTTP (ASGI transport), covering RBAC,
file validation, and the 202-without-parsing contract."""

import io

import docx
import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

pytestmark = pytest.mark.asyncio

_DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_and_create_tender(client: AsyncClient, email: str, org_name: str) -> tuple:
    register = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = register.json()
    token = data["access_token"]
    tender = await client.post(
        f"/organizations/{data['organization_id']}/tenders",
        json={"name": "Docs API RFP"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, tender.json()["id"]


def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_paragraph("Bidders must hold a valid tax clearance certificate.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


async def test_upload_returns_202_and_does_not_expose_storage_key():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-1@example.com", "Docs API Org 1"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp.docx", _docx_bytes(), _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 202
        body = resp.json()
        assert body["parse_status"] == "uploaded", "upload must not wait for parsing"
        # "rfp.docx" trips the filename-keyword rule, which deliberately wins
        # over the .docx extension default of "annexure".
        assert body["doc_type"] == "rfp"
        assert "storage_key" not in body, "internal object path must not leak (A5.1)"
        assert len(body["sha256"]) == 64


async def test_duplicate_upload_returns_200_with_the_same_document():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-2@example.com", "Docs API Org 2"
        )
        content = _docx_bytes()
        headers = {"Authorization": f"Bearer {token}"}

        first = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp.docx", content, _DOCX_TYPE)},
            headers=headers,
        )
        second = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp-copy.docx", content, _DOCX_TYPE)},
            headers=headers,
        )

        assert first.status_code == 202
        assert second.status_code == 200, "a duplicate is not newly accepted work"
        assert second.json()["id"] == first.json()["id"]

        listed = await client.get(f"/tenders/{tender_id}/documents", headers=headers)
        assert len(listed.json()) == 1


async def test_unsupported_file_type_is_rejected():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-3@example.com", "Docs API Org 3"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("notes.txt", b"plain text", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 415


async def test_generic_octet_stream_content_type_is_accepted():
    """curl without /etc/mime.types, plain httpx/requests calls, and some
    browsers all send application/octet-stream for a valid .docx. Treating
    that as a mismatch would 415 legitimate uploads - found by the live
    smoke test, which is why this regression test exists."""
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-octet@example.com", "Docs API Octet Org"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("annexure.docx", _docx_bytes(), "application/octet-stream")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202, resp.text

        # And with no content-type asserted at all.
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("other.xlsx", b"PK\x03\x04 minimal", None)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 202, resp.text


async def test_mismatched_content_type_is_rejected():
    """Extension and content-type must agree - a .pdf carrying a docx
    content-type would reach a parser that cannot read it."""
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-4@example.com", "Docs API Org 4"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp.pdf", _docx_bytes(), _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 415


async def test_empty_file_is_rejected():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-5@example.com", "Docs API Org 5"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("empty.docx", b"", _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400


async def test_documents_are_not_visible_across_organizations():
    """Retrieval must never cross an organization boundary - the same
    isolation B2 established for tenders, now via the document_id branch of
    _resolve_organization_id."""
    async with await _client() as client:
        owner_token, tender_id = await _register_and_create_tender(
            client, "docs-api-owner@example.com", "Docs API Owner Org"
        )
        upload = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp.docx", _docx_bytes(), _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        document_id = upload.json()["id"]

        outsider = await client.post(
            "/auth/register",
            json={
                "email": "docs-api-outsider@example.com",
                "password": "hunter2",
                "organization_name": "Docs API Outsider Org",
            },
        )
        outsider_token = outsider.json()["access_token"]

        blocked = await client.get(
            f"/documents/{document_id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert blocked.status_code == 403

        allowed = await client.get(
            f"/documents/{document_id}",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert allowed.status_code == 200


async def test_reprocess_requeues_and_marks_document_queued():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-6@example.com", "Docs API Org 6"
        )
        headers = {"Authorization": f"Bearer {token}"}
        upload = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("rfp.docx", _docx_bytes(), _DOCX_TYPE)},
            headers=headers,
        )
        document_id = upload.json()["id"]

        resp = await client.post(f"/documents/{document_id}/reprocess", headers=headers)

        assert resp.status_code == 202
        assert resp.json()["parse_status"] == "queued"
