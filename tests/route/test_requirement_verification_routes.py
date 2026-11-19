"""Plan item 59, Q5 (PR 21), at the HTTP level: the requirement register's
rows carry when each was last checked, so the screen can show "Not verified
yet" and "Verification failed" apart.

The register's own data is GET /tenders/{id}/requirements; the badge itself
is the web's (RequirementRegister.tsx, statusMap.ts), tested there.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio

_TEXT = "The bidder shall hold ISO 27001."


async def _requirement(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="rfp.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text=_TEXT,
            normalized_text=_TEXT.lower(),
            content_hash=uuid.uuid4().hex,
        )
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code=f"SEC-{uuid.uuid4().hex[:4]}",
            requirement_text=_TEXT,
            normalized_text=_TEXT.lower(),
            category="security",
            mandatory=True,
            source_chunk_id=chunk.id,
            content_fingerprint=uuid.uuid4().hex,
        )
        await uow.commit()
        return requirement.id


async def test_the_register_rows_carry_when_each_was_checked(client: TestClient, register_actor):
    headers, _, org_id = register_actor()
    created = client.post(
        f"/organizations/{org_id}/tenders", json={"name": "Verification bid"}, headers=headers
    )
    assert created.status_code == 201, created.text
    tender_id = uuid.UUID(created.json()["id"])
    failed = await _requirement(tender_id)
    never = await _requirement(tender_id)
    async with UnitOfWork() as uow:
        await uow.requirements.set_verdict_for_text(failed, False, _TEXT)
        await uow.commit()

    response = client.get(f"/tenders/{tender_id}/requirements", headers=headers)

    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()}
    assert rows[str(failed)]["verified"] is False
    assert rows[str(failed)]["verification_checked_at"] is not None
    assert rows[str(never)]["verified"] is False
    assert rows[str(never)]["verification_checked_at"] is None
