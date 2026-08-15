"""domain/services/commercial_service.py against real Postgres/MinIO and a
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

    # Sheet1 alone: 4 findings, called twice = 8 explanation calls scripted,
    # even though only 4 distinct findings should ever be persisted.
    client = _fake_client_scripted_for(8)

    first = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)
    second = await commercial_service.evaluate_commercial_for_tender(client, "fake-model", tender_id)

    assert len(first) == 4
    assert {f.id for f in first} == {f.id for f in second}, "retry must not duplicate findings"
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
