"""Phase 4 consumer guard for verify_requirement_job.

The outbox delivers RequirementVerificationRequested at least once. A
second delivery of the same event must not repeat the LLM verification
call; any other request (a different event) must still run, including one
made while an earlier verification was still in flight. "Already served"
means a completed verification trace carries this exact event id.

(The guards for resume_workflow, process_knowledge_document_import and
parse_and_embed_document are tested next to their existing suites:
test_graph_execution.py, test_knowledge_import_worker_job.py and
test_document_ingestion.py.)
"""

import io
import uuid

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.requirements import VerificationOutput
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_REQUIREMENT_VERIFICATION_REQUESTED
from tenderos_backend.domain.services import document_service, requirement_service
from tenderos_backend.domain.services.requirement_service import RequirementDraft
from tenderos_worker.jobs import verification as verification_job
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


# Same two helpers as test_requirement_service.py, duplicated rather than
# imported for the reason tests/route/conftest.py gives (pytest's prepend
# import mode does not make sibling test modules reliably importable).
async def _make_tender() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Consumer Guard Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Consumer Guard Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return org.id, tender.id


async def _make_chunk_id(tender_id: uuid.UUID, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    doc = docx.Document()
    doc.add_paragraph("Bidders must hold a valid tax clearance certificate.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="annexure.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
    assert chunks, "fixture document must produce at least one chunk"
    return document.id, chunks[0].id


async def _verification_requests(requirement_id: uuid.UUID) -> list[OutboxEvent]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == requirement_id,
                OutboxEvent.event_type == EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
            )
            .order_by(OutboxEvent.created_at)
        )
        return list(result.scalars().all())


async def test_a_duplicate_verification_makes_no_llm_call_but_a_request_made_in_flight_runs(
    monkeypatch,
):
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    [requirement] = await requirement_service.persist_extraction_results(
        tender_id,
        [
            RequirementDraft(
                requirement_text="[REQ-001] The bidder must hold a valid tax clearance certificate.",
                category="eligibility",
                mandatory=True,
                source_chunk_id=chunk_id,
                document_id=document_id,
                confidence=0.9,
            )
        ],
    )

    fake = FakeLLMClient()
    fake.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.9,
                reasoning_summary="The source chunk states the obligation.",
            )
        ]
        * 4,
    )
    monkeypatch.setattr(verification_job, "_build_llm_client", lambda: fake)

    # Two requests, the second made while the first is still in flight: both
    # events exist before the first verification runs and finishes.
    await requirement_service.request_verification(requirement.id)
    await requirement_service.request_verification(requirement.id)
    first_request, second_request = await _verification_requests(requirement.id)

    await verification_job.verify_requirement_job(str(requirement.id), str(first_request.id))
    assert len(fake.calls) == 1

    # The same event delivered again: already served, no LLM call.
    await verification_job.verify_requirement_job(str(requirement.id), str(first_request.id))
    assert len(fake.calls) == 1

    # The second request still runs, although the first one's verification
    # finished after it was made. (A "verified since this event was created"
    # rule skipped it, so a change between the two requests went unverified.)
    await verification_job.verify_requirement_job(str(requirement.id), str(second_request.id))
    assert len(fake.calls) == 2

    # And its own duplicate is skipped too.
    await verification_job.verify_requirement_job(str(requirement.id), str(second_request.id))
    assert len(fake.calls) == 2


async def test_a_job_queued_before_the_guard_still_runs(monkeypatch):
    """A job enqueued with the old one-argument shape (no event id) cannot
    be checked against an event, so it runs as it always did."""
    org_id, tender_id = await _make_tender()
    document_id, chunk_id = await _make_chunk_id(tender_id, org_id)
    [requirement] = await requirement_service.persist_extraction_results(
        tender_id,
        [
            RequirementDraft(
                requirement_text="[REQ-002] The bidder must submit audited accounts.",
                category="eligibility",
                mandatory=True,
                source_chunk_id=chunk_id,
                document_id=document_id,
                confidence=0.9,
            )
        ],
    )
    fake = FakeLLMClient()
    fake.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.9,
                reasoning_summary="The source chunk states the obligation.",
            )
        ]
        * 2,
    )
    monkeypatch.setattr(verification_job, "_build_llm_client", lambda: fake)

    await verification_job.verify_requirement_job(str(requirement.id))
    await verification_job.verify_requirement_job(str(requirement.id))
    assert len(fake.calls) == 2
