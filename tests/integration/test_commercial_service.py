"""domain/services/commercial_service.py against real Postgres/S3 and a
scripted FakeLLMClient - ingestion idempotency, the full ingest -> check ->
explain -> persist pipeline against the real synthetic pricing workbooks,
and resolve_pricing_finding's audit trail."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.compliance import EvidenceLink
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import commercial_service, document_service

pytestmark = pytest.mark.asyncio

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"


async def _make_org_and_tender(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _upload_pricing_sheet(org_id: uuid.UUID, tender_id: uuid.UUID, filename: str) -> uuid.UUID:
    content = (FIXTURES_DIR / filename).read_bytes()
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    return document.id


def _fake_client_scripted_for(n_calls: int) -> FakeLLMClient:
    client = FakeLLMClient()
    client.script(
        "commercial_explanation",
        FindingExplanation,
        [
            FindingExplanation(severity="major", explanation="A deterministic pricing check found this issue.")
            for _ in range(n_calls)
        ],
    )
    return client


async def test_ingest_pricing_workbook_is_idempotent_on_retry():
    org_id, tender_id = await _make_org_and_tender("Commercial Ingest Test")
    document_id = await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")

    first = await commercial_service.ingest_pricing_workbook(document_id, tender_id)
    second = await commercial_service.ingest_pricing_workbook(document_id, tender_id)

    assert len(first) == 1  # one sheet
    assert first[0].id == second[0].id
    async with UnitOfWork() as uow:
        items = await uow.commercial.list_line_items_for_pricing_document(first[0].id)
    # 5 data rows (unit_cost + quantity + line_total + currency each) + 1 grand total cell
    assert len(items) == 5 * 4 + 1, f"unexpected line item count after two ingests: {len(items)}"


async def test_evaluate_commercial_for_tender_persists_findings_with_explanations():
    org_id, tender_id = await _make_org_and_tender("Commercial Eval Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_2.xlsx")

    # 4 findings on sheet1 + 3 on sheet2 + 1 cross-doc = 8 total LLM explanation calls.
    client = _fake_client_scripted_for(8)

    findings = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)

    assert len(findings) == 8
    finding_types = {f.finding_type for f in findings}
    assert finding_types == {
        "wrong_total",
        "duplicate_cost",
        "currency_mismatch",
        "needs_human_review",
        "missing_cell",
        "cross_doc_mismatch",
    }
    for f in findings:
        assert "A deterministic pricing check found this issue." in f.note, (
            "the LLM's explanation must be folded into the persisted note"
        )
        assert f.resolved is False


async def test_evaluate_commercial_for_tender_is_idempotent_on_retry():
    org_id, tender_id = await _make_org_and_tender("Commercial Retry Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")

    # Sheet1 alone: 4 findings. 8 explanations are scripted so that a retry
    # that wrongly explained again would still get answers; the call count
    # below is what proves it does not.
    client = _fake_client_scripted_for(8)

    first = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)
    calls_after_first = len(client.calls)
    second = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)

    assert len(first) == 4
    assert {f.id for f in first} == {f.id for f in second}, "retry must not duplicate findings"
    # Phase 4 Stage 2: every finding was already stored, so the retry looked
    # each one up by fingerprint and made no LLM call at all.
    assert calls_after_first == 4
    assert len(client.calls) == 4, "an already-stored finding must not be explained again"
    async with UnitOfWork() as uow:
        all_findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
    assert len(all_findings) == 4


async def test_resolve_pricing_finding_writes_audit_event():
    org_id, tender_id = await _make_org_and_tender("Commercial Resolve Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    client = _fake_client_scripted_for(4)
    findings = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)
    actor_id = uuid.uuid4()

    updated = await commercial_service.resolve_pricing_finding(findings[0].id, actor_id)

    assert updated.resolved is True
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(AuditEvent).where(
                AuditEvent.target_type == "pricing_finding", AuditEvent.target_id == findings[0].id
            )
        )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].action == "pricing_finding_resolve"
    assert events[0].actor_id == str(actor_id)


async def _evidence_links_for_tender(tender_id: uuid.UUID) -> list[EvidenceLink]:
    async with UnitOfWork() as uow:
        return await uow.compliance.list_evidence_links_for_tender(tender_id)


async def test_resolve_pricing_finding_materializes_a_document_excerpt_evidence_link():
    """B16 Group 6, Option C: a resolved single-document finding (every
    finding_type except cross_doc_mismatch) becomes one real, citable
    EvidenceLink - a synthetic document_excerpt chunk quoting the
    finding's own note, attached to the real uploaded pricing sheet."""
    org_id, tender_id = await _make_org_and_tender("Pricing Evidence Single-Doc Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    client = _fake_client_scripted_for(4)
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, "fake-model", tender_id
    )
    single_doc_finding = next(f for f in findings if f.finding_type != "cross_doc_mismatch")
    assert single_doc_finding.pricing_document_id is not None, (
        "every single-document finding must be tagged with its source pricing_document_id"
    )

    await commercial_service.resolve_pricing_finding(single_doc_finding.id, uuid.uuid4())

    links = await _evidence_links_for_tender(tender_id)
    assert len(links) == 1
    assert links[0].source_kind == "document_excerpt"
    assert links[0].source_chunk_id is not None
    assert links[0].verified is True
    assert links[0].validity_status == "valid"
    async with UnitOfWork() as uow:
        chunk = await uow.documents.get_chunk(links[0].source_chunk_id)
    assert chunk is not None
    assert single_doc_finding.note in chunk.chunk_text


async def test_resolve_pricing_finding_materializes_two_links_for_cross_doc_mismatch():
    """cross_doc_mismatch is the one finding type genuinely about two
    documents at once - item_a_ref/item_b_ref are symmetric, so this
    must produce two evidence links (one per document), not one
    arbitrarily chosen side."""
    org_id, tender_id = await _make_org_and_tender("Pricing Evidence Cross-Doc Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_2.xlsx")
    client = _fake_client_scripted_for(8)
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, "fake-model", tender_id
    )
    cross_doc_finding = next(f for f in findings if f.finding_type == "cross_doc_mismatch")
    assert cross_doc_finding.pricing_document_id is None, (
        "cross_doc_mismatch must not get a single pricing_document_id - it has two, "
        "named symmetrically via item_a_ref/item_b_ref instead"
    )

    await commercial_service.resolve_pricing_finding(cross_doc_finding.id, uuid.uuid4())

    links = await _evidence_links_for_tender(tender_id)
    assert len(links) == 2
    assert {link.source_kind for link in links} == {"document_excerpt"}
    async with UnitOfWork() as uow:
        chunks = [await uow.documents.get_chunk(link.source_chunk_id) for link in links]
    document_ids = {chunk.document_id for chunk in chunks if chunk is not None}
    assert len(document_ids) == 2, "the two links must point at the two DIFFERENT documents"


async def test_resolve_pricing_finding_twice_does_not_duplicate_evidence():
    """resolve_pricing_finding_in_uow has no guard against being called
    again on an already-resolved finding - evidence materialization must
    be idempotent (checked by fingerprint), not crash or double the
    evidence link count on a repeat resolve action."""
    org_id, tender_id = await _make_org_and_tender("Pricing Evidence Repeat Resolve Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    client = _fake_client_scripted_for(4)
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, "fake-model", tender_id
    )
    single_doc_finding = next(f for f in findings if f.finding_type != "cross_doc_mismatch")

    await commercial_service.resolve_pricing_finding(single_doc_finding.id, uuid.uuid4())
    await commercial_service.resolve_pricing_finding(single_doc_finding.id, uuid.uuid4())

    links = await _evidence_links_for_tender(tender_id)
    assert len(links) == 1, "a repeat resolve must not create a second evidence link"
