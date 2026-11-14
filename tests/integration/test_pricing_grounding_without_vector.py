"""Plan item 82: grounding a resolved pricing finding stores its synthetic
chunk with no vector, and loads no model.

The first pricing-finding resolve on a fresh api process used to import
torch and load the embedding model inside the request: 8.6 to 9.7 s on the
fake stack (measured), against the frozen E2E's 10 s wait. No code reads that
chunk's vector: a pricing document is a tender document (organization_id
NULL) and the only vector search filters on Document.organization_id, then
keeps only chunks a knowledge item cites. So the grounding now skips the
embedding (KNOWN_LIMITATIONS.md, plan item 82).

Covered here, against real Postgres:
- both routes that ground a finding, the resolve route's service call and a
  gate decision through gate_target_service, store the chunk with no vector
  and still create the evidence link;
- grounding through the service in a fresh interpreter leaves torch,
  transformers and sentence_transformers out of sys.modules (this pytest
  process has them loaded by other tests already);
- other callers of create_synthetic_chunk still get a vector by default.
"""

import json
import os
import subprocess
import sys
import uuid
from datetime import date

import pytest
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    commercial_service,
    document_service,
    gate_target_service,
)

pytestmark = pytest.mark.asyncio

_HEAVY = ("torch", "transformers", "sentence_transformers")

_NOTE = "Unit price for item 4 is 120.00 in the schedule but 150.00 in the summary."

# Resolves one finding through the service, as the route does, in a fresh
# interpreter, then prints which heavy modules that loaded.
_RESOLVE_IN_FRESH_INTERPRETER = """
import asyncio, json, sys, uuid
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
heavy = json.loads(sys.argv[1])
finding_id = uuid.UUID(sys.argv[2])
from tenderos_backend.db.session import engine
from tenderos_backend.domain.services import commercial_service

async def main():
    await commercial_service.resolve_pricing_finding(finding_id, uuid.uuid4())
    await engine.dispose()

asyncio.run(main())
print(json.dumps(sorted(h for h in heavy if h in sys.modules)))
"""


async def _tender() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"Grounding {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(
            organization_id=org.id, name="Grounding Tender", submission_date=date(2026, 12, 1)
        )
        session.add(tender)
        await session.flush()
        await session.commit()
        return tender.id


async def _finding_with_a_pricing_document(tender_id: uuid.UUID) -> uuid.UUID:
    # A tender document, as every uploaded pricing sheet is (organization_id
    # NULL), its pricing_documents row, and a single-document finding on it.
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="pricing.xlsx",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        pricing_document = await uow.commercial.create_pricing_document(
            tender_id=tender_id, document_id=document.id, sheet_name="Schedule"
        )
        finding = await uow.commercial.create_pricing_finding(
            tender_id=tender_id,
            finding_type="wrong_total",
            note=_NOTE,
            content_fingerprint=uuid.uuid4().hex,
            pricing_document_id=pricing_document.id,
        )
        await uow.commit()
        return finding.id


async def _grounding_chunks(tender_id: uuid.UUID) -> list:
    async with UnitOfWork() as uow:
        links = await uow.compliance.list_evidence_links_for_tender(tender_id)
        return [await uow.documents.get_chunk(link.source_chunk_id) for link in links]


async def _user() -> uuid.UUID:
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def test_resolving_a_pricing_finding_stores_its_chunk_without_a_vector():
    tender_id = await _tender()
    finding_id = await _finding_with_a_pricing_document(tender_id)

    await commercial_service.resolve_pricing_finding(finding_id, uuid.uuid4())

    chunks = await _grounding_chunks(tender_id)
    assert len(chunks) == 1, "the evidence link and its chunk must still be created"
    assert chunks[0] is not None
    assert chunks[0].chunk_text == _NOTE
    assert chunks[0].embedding is None


async def test_a_gate_decision_resolving_a_pricing_finding_stores_its_chunk_without_a_vector():
    tender_id = await _tender()
    finding_id = await _finding_with_a_pricing_document(tender_id)

    await gate_target_service.apply_target_updates(
        tender_id,
        [{"target_type": "pricing_finding", "target_id": str(finding_id), "decision": "approve"}],
        await _user(),
    )

    async with UnitOfWork() as uow:
        finding = await uow.commercial.get_pricing_finding(finding_id)
    assert finding is not None and finding.resolved is True
    chunks = await _grounding_chunks(tender_id)
    assert len(chunks) == 1
    assert chunks[0] is not None
    assert chunks[0].embedding is None


async def test_grounding_a_pricing_finding_loads_no_torch():
    tender_id = await _tender()
    finding_id = await _finding_with_a_pricing_document(tender_id)

    result = subprocess.run(
        [sys.executable, "-c", _RESOLVE_IN_FRESH_INTERPRETER, json.dumps(_HEAVY), str(finding_id)],
        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, result.stderr
    # The grounding really ran in that interpreter: its chunk exists.
    chunks = await _grounding_chunks(tender_id)
    assert len(chunks) == 1
    assert chunks[0] is not None
    assert json.loads(result.stdout.strip().splitlines()[-1]) == []


async def test_other_synthetic_chunks_still_get_a_vector_by_default():
    # Certification import (knowledge_service) relies on the default: a
    # knowledge item cites its chunk, so retrieval needs the vector.
    tender_id = await _tender()
    async with UnitOfWork() as uow:
        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="cert.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await document_service.create_synthetic_chunk(
            uow, document.id, "ISO 27001 certificate, valid until 2027."
        )
        await uow.commit()

    assert chunk.embedding is not None
    assert len(chunk.embedding) == 384
