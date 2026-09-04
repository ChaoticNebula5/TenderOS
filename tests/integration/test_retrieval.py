"""agents/knowledge/retrieval.py::search_company_knowledge - real Postgres,
real embeddings (no LLM in this path at all). Focused on valid_only, which
had zero coverage before this file (B16): only the valid_until half was
ever checked, never the knowledge_item.status half - the exact
"capability exists, nothing exercises it" shape this project keeps
finding. Self-built fixture helpers, no cross-test-module imports, matching
this codebase's own established convention.
"""

import io
import uuid
from datetime import date

import docx
import pytest
from tenderos_backend.agents.knowledge.retrieval import search_company_knowledge
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_org() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"Retrieval Test Org {uuid.uuid4().hex[:8]}")
        session.add(org)
        await session.flush()
        await session.commit()
        return org.id


async def _make_company_chunk(org_id: uuid.UUID, text: str) -> uuid.UUID:
    # side="company" (not the default "client") is what makes
    # search_chunks_lexical_for_org/search_chunks_vector_for_org able to
    # find this chunk at all - both filter on Document.organization_id,
    # which create_document silently drops whenever tender_id is also
    # given (its own tender_id-XOR-organization_id invariant). Confirmed
    # directly while building test_reanalysis_job.py's own company-chunk
    # helper, same session - not re-derived from scratch here.
    doc = docx.Document()
    doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="company_knowledge.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        side="company",
        doc_type="company_document",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    return chunks[0].id


async def test_valid_only_excludes_an_archived_status_item_with_no_valid_until():
    """The specific gap: valid_only=True only ever checked valid_until (a
    date comparison) - an archived-status item with valid_until left unset
    entirely had nothing to disqualify it. Same knowledge-base text as the
    query, so retrieval finds it easily; the assertion is entirely about
    whether it's FILTERED, not about ranking quality.
    """
    org_id = await _make_org()
    text = "The company maintains ISO 27001 certification for information security."
    chunk_id = await _make_company_chunk(org_id, text)

    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="ISO 27001",
            content_fingerprint=str(uuid.uuid4()),
        )
        await uow.knowledge.associate_knowledge_item_chunks(knowledge_item.id, [chunk_id])
        await uow.knowledge.update_knowledge_item(knowledge_item.id, status="archived")
        await uow.commit()

    results_unfiltered = await search_company_knowledge(org_id, text, valid_only=False)
    assert any(r.knowledge_item_id == knowledge_item.id for r in results_unfiltered), (
        "sanity check: the archived item must be a real retrieval candidate at all, "
        "otherwise this test would pass for the wrong reason"
    )

    results_valid_only = await search_company_knowledge(org_id, text, valid_only=True)
    assert not any(r.knowledge_item_id == knowledge_item.id for r in results_valid_only), (
        "an archived-status item must not survive valid_only=True even with no "
        "valid_until set to disqualify it"
    )


async def test_valid_only_still_includes_an_active_status_item():
    # Regression guard: valid_only must not become "exclude everything."
    org_id = await _make_org()
    text = "The company holds a current professional indemnity insurance policy."
    chunk_id = await _make_company_chunk(org_id, text)

    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Professional Indemnity Insurance",
            content_fingerprint=str(uuid.uuid4()),
        )
        await uow.knowledge.associate_knowledge_item_chunks(knowledge_item.id, [chunk_id])
        await uow.commit()

    results = await search_company_knowledge(org_id, text, valid_only=True)
    assert any(r.knowledge_item_id == knowledge_item.id for r in results)


async def test_valid_only_still_excludes_expired_valid_until_regression():
    # Regression guard for the half this file didn't touch - valid_until
    # must still work exactly as before this status check was added.
    org_id = await _make_org()
    text = "The company's data protection certification covers all client engagements."
    chunk_id = await _make_company_chunk(org_id, text)

    async with UnitOfWork() as uow:
        knowledge_item, _cert = await uow.knowledge.create_certification(
            organization_id=org_id,
            title="Data Protection Cert",
            content_fingerprint=str(uuid.uuid4()),
            valid_until=date(2020, 1, 1),
        )
        await uow.knowledge.associate_knowledge_item_chunks(knowledge_item.id, [chunk_id])
        await uow.commit()

    results = await search_company_knowledge(org_id, text, valid_only=True, today=date(2026, 1, 1))
    assert not any(r.knowledge_item_id == knowledge_item.id for r in results)
