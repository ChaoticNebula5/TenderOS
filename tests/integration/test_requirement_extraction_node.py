"""requirement_extraction_node against real chunks and a scripted
FakeLLMClient - task creation, persistence, the mandatory-classifier/LLM
combination, and the extract-then-verify pass over mandatory items."""

import io
import uuid

import docx
import pytest
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.requirements import (
    ExtractedRequirement,
    ExtractionOutput,
    VerificationOutput,
)
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.models.workflow import WorkflowTask
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.orchestration.nodes import requirement_extraction
from tenderos_backend.orchestration.nodes.requirement_extraction import (
    requirement_extraction_node,
)
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


async def _make_tender_with_chunk() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name="Extraction Node Test Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Extraction Node Test Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        org_id, tender_id = org.id, tender.id

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
    return org_id, tender_id


def _base_state(tender_id: uuid.UUID, workflow_run_id: uuid.UUID) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": "unused-by-this-node",
        "workflow_run_id": str(workflow_run_id),
        "langgraph_thread_id": str(workflow_run_id),
        "status": "extracting_requirements",
        "plan_version": 0,
        "active_gate_id": None,
        "revision_count": 0,
        "max_revisions": 2,
        "retry_budget_remaining": 10,
        "dispatch_task_ids": [],
        "completed_task_ids": [],
        "failed_task_ids": [],
        "unverified_requirement_ids": [],
        "open_critical_finding_ids": [],
        "changed_knowledge_item_ids": [],
        "readiness_report_id": None,
        "graph_projection_version": None,
        "degraded_stages": [],
    }


async def _make_workflow_run_row(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()
        return run.id


async def test_node_persists_requirements_and_marks_task_completed(monkeypatch):
    _org_id, tender_id = await _make_tender_with_chunk()
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    fake_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [
            ExtractionOutput(
                requirements=[
                    ExtractedRequirement(
                        requirement_text="Bidders must hold a valid tax clearance certificate.",
                        category="eligibility",
                        mandatory=True,
                        source_chunk_ref="chunk_1",
                        confidence=0.9,
                    )
                ]
            )
        ],
    )
    fake_client.script(
        "requirement_verification",
        VerificationOutput,
        [
            VerificationOutput(
                supported_by_source=True,
                mandatory_language_supported=True,
                verification_confidence=0.95,
                reasoning_summary="Source text directly states this requirement.",
            )
        ],
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)

    result = await requirement_extraction_node(_base_state(tender_id, workflow_run_id))

    assert result == {"status": "awaiting_requirement_review"}

    async with UnitOfWork() as uow:
        requirements = await uow.requirements.list_for_tender(tender_id)
        task = await uow.workflow.get_task_by_key(
            workflow_run_id, f"requirement_extraction:{workflow_run_id}"
        )

    assert len(requirements) == 1
    assert requirements[0].mandatory is True
    assert requirements[0].verified is True, "mandatory item must go through extract-then-verify"
    assert task is not None
    assert task.status == "completed"
    assert task.output_refs["requirement_count"] == 1


async def test_node_does_not_duplicate_task_on_rerun(monkeypatch):
    _org_id, tender_id = await _make_tender_with_chunk()
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    fake_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [ExtractionOutput(requirements=[], submission_items=[], clarifications=[])] * 2,
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)

    state = _base_state(tender_id, workflow_run_id)
    await requirement_extraction_node(state)
    await requirement_extraction_node(state)

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(WorkflowTask).where(
                WorkflowTask.workflow_run_id == workflow_run_id,
                WorkflowTask.task_type == "requirement_extraction",
            )
        )
    assert len(result.scalars().all()) == 1


async def test_node_marks_task_failed_when_extraction_raises(monkeypatch):
    _org_id, tender_id = await _make_tender_with_chunk()
    workflow_run_id = await _make_workflow_run_row(tender_id)

    # No script() call for this (agent_name, schema) pair - FakeLLMClient
    # raises AssertionError on the first call, standing in for any real
    # extraction failure the node must convert into a failed task + reraise.
    fake_client = FakeLLMClient()
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)

    with pytest.raises(AssertionError):
        await requirement_extraction_node(_base_state(tender_id, workflow_run_id))

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(
            workflow_run_id, f"requirement_extraction:{workflow_run_id}"
        )
    assert task is not None
    assert task.status == "failed"


async def test_node_marks_task_failed_when_client_construction_itself_raises(monkeypatch):
    """Regression test: _build_llm_client() failing (e.g. a missing/invalid
    API key - GeminiClient.__init__ raises before any network call) must
    still mark the task failed, same as a failure inside extraction itself.
    Caught live against Docker: _build_llm_client() was originally called
    BEFORE the try block, so this exact failure mode left the task stuck at
    'pending' forever with no error recorded - a bug the FakeLLMClient-based
    test above couldn't catch, since monkeypatching _build_llm_client to
    return a working fake never exercises the real function failing.
    """
    _org_id, tender_id = await _make_tender_with_chunk()
    workflow_run_id = await _make_workflow_run_row(tender_id)

    def _raise() -> None:
        raise ValueError("No API key was provided.")

    monkeypatch.setattr(requirement_extraction, "_build_llm_client", _raise)

    with pytest.raises(ValueError):
        await requirement_extraction_node(_base_state(tender_id, workflow_run_id))

    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(
            workflow_run_id, f"requirement_extraction:{workflow_run_id}"
        )
    assert task is not None
    assert task.status == "failed"
    assert task.error_code == "ValueError"
