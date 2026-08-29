"""G2's document endpoints over real HTTP (ASGI transport), covering RBAC,
file validation, and the 202-without-parsing contract."""

import io
import zipfile

import docx
import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
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


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    workbook.active["A1"] = "Item"
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _zip_bytes(members: dict[str, bytes], compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
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

        # And with no content-type asserted at all. Real xlsx bytes now,
        # not a hand-written b"PK\x03\x04 minimal" placeholder - B15 added
        # real content sniffing (documents.py::_sniff_and_check_archive),
        # which correctly rejects that placeholder as not a real archive.
        # This test's own point (an unasserted Content-Type is accepted)
        # is unrelated to that check and still needs a genuinely valid file
        # to prove it against.
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("other.xlsx", _xlsx_bytes(), None)},
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


async def test_content_that_does_not_match_the_claimed_type_is_rejected():
    """B15 real content sniffing: extension AND Content-Type header can both
    be correct while the bytes themselves are not actually a docx - this is
    exactly the gap the old extension/header-only check could never catch."""
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-sniff-1@example.com", "Docs API Sniff Org 1"
        )

        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("fake.docx", b"not actually a zip archive at all", _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 415, resp.text


async def test_a_real_zip_missing_the_expected_internal_structure_is_rejected():
    """PK\\x03\\x04 magic bytes alone aren't enough - any zip has those.
    A real docx must contain word/document.xml specifically."""
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-sniff-2@example.com", "Docs API Sniff Org 2"
        )

        plain_zip = _zip_bytes({"readme.txt": b"just an ordinary zip, not a docx"})
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("fake.docx", plain_zip, _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 415, resp.text


async def test_archive_with_too_many_entries_is_rejected():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-bomb-entries@example.com", "Docs API Bomb Entries Org"
        )

        members = {"word/document.xml": b"<w:document/>"}
        members.update({f"junk/{i}.txt": b"x" for i in range(1001)})
        bomb = _zip_bytes(members)
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("bomb.docx", bomb, _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 413, resp.text


async def test_archive_with_an_excessive_compression_ratio_is_rejected():
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-bomb-ratio@example.com", "Docs API Bomb Ratio Org"
        )

        # Highly repetitive text compresses far past the 200x ceiling
        # (real docx/xlsx content tops out around 24x - see documents.py's
        # own measured-fixture comment). 5MB of a single repeated byte is
        # enough to clear 200x under DEFLATE without a slow multi-second
        # test fixture.
        members = {
            "word/document.xml": b"A" * 5_000_000,
        }
        bomb = _zip_bytes(members, compression=zipfile.ZIP_DEFLATED)
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("bomb.docx", bomb, _DOCX_TYPE)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 413, resp.text


async def test_oversized_upload_is_rejected_before_being_fully_read(monkeypatch):
    """Confirms the bounded-read fix (documents.py::_read_bounded): a file
    over the limit is rejected via the same 413 path, not read unbounded
    first. max_upload_size_mb is monkeypatched down so this doesn't need a
    real 50MB fixture to exercise the boundary."""
    monkeypatch.setattr("tenderos_api.routes.documents.settings.max_upload_size_mb", 1)
    async with await _client() as client:
        token, tender_id = await _register_and_create_tender(
            client, "docs-api-oversized@example.com", "Docs API Oversized Org"
        )

        oversized = b"a,b,c\r\n" * 200_000  # well over 1 MB
        resp = await client.post(
            f"/tenders/{tender_id}/documents",
            files={"file": ("big.csv", oversized, "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 413, resp.text
