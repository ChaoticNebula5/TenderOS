"""talent_analysis_node against a real staffing requirement, a real
candidate pool, and scripted FakeLLMClients for both role_extraction and
talent_scoring - task completion and one role_requirements + role_matches
row produced end to end."""

import io
import uuid

import docx
import pytest
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.talent import (
    CandidateScoring,
    ExtractedRole,
    RoleExtractionOutput,
    TalentStrength,
)
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.orchestration.nodes import talent
from tenderos_backend.orchestration.nodes.talent import talent_analysis_node
from tenderos_worker.jobs.ingestion import parse_and_embed_document

pytestmark = pytest.mark.asyncio


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


async def _make_staffing_requirement(org_id: uuid.UUID, tender_id: uuid.UUID) -> uuid.UUID:
    doc = docx.Document()
    doc.add_paragraph("The bidder must assign a dedicated Project Manager.")
    buf = io.BytesIO()
    doc.save(buf)
    document, _ = await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename="rfp.docx",
        content=buf.getvalue(),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    await parse_and_embed_document(str(document.id))
    async with UnitOfWork() as uow:
        chunks = await uow.documents.list_chunks_for_document(document.id)
        requirement = await uow.requirements.create(
            tender_id=tender_id,
            display_code="REQ-STAFF-1",
            requirement_text="The bidder must assign a dedicated Project Manager.",
            normalized_text="the bidder must assign a dedicated project manager",
            category="staffing",
            mandatory=True,
            source_chunk_id=chunks[0].id,
            content_fingerprint=str(uuid.uuid4()),
        )
        await uow.commit()
        return requirement.id


async def _make_candidate(org_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        knowledge_item, employee = await uow.knowledge.create_employee(
            organization_id=org_id,
            name="Casey Candidate",
            content_fingerprint=str(uuid.uuid4()),
            title="Casey Candidate",
        )
        await uow.knowledge.update_employee(
            employee.knowledge_item_id,
            eligible_for_bid=True,
            profile_review_status="verified",
            availability_status="available",
        )
        await uow.commit()
        return knowledge_item.id


def _base_state(tender_id: uuid.UUID, organization_id: uuid.UUID, workflow_run_id: uuid.UUID) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": str(organization_id),
        "workflow_run_id": str(workflow_run_id),
        "langgraph_thread_id": str(workflow_run_id),
        "status": "running_specialists",
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
    }


async def _make_workflow_run_row(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()
        return run.id


async def _make_talent_task(workflow_run_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=workflow_run_id,
            task_key=f"talent_analysis:{workflow_run_id}",
            task_type="talent_analysis",
            assigned_agent="talent",
            priority=5,
            input_refs={},
        )
        await uow.commit()
        return task.id


async def test_node_extracts_one_role_and_scores_one_candidate(monkeypatch):
    org_id, tender_id = await _make_org_and_tender("Talent Node Test")
    await _make_staffing_requirement(org_id, tender_id)
    candidate_item_id = await _make_candidate(org_id)
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    fake_client.script(
        "role_extraction",
        RoleExtractionOutput,
        [
            RoleExtractionOutput(
                roles=[
                    ExtractedRole(
                        role_name="Project Manager",
                        required_skills=["Stakeholder management"],
                        source_requirement_refs=["req_1"],
                    )
                ]
            )
        ],
    )
    fake_client.script(
        "talent_scoring",
        CandidateScoring,
        [
            CandidateScoring(
                score=0.75,
                strengths=[TalentStrength(criterion="PM experience", evidence_ref="evidence_1")],
                gaps=[],
            )
        ],
    )
    monkeypatch.setattr(talent, "_build_llm_client", lambda: fake_client)

    task_id = await _make_talent_task(workflow_run_id)
    state = {**_base_state(tender_id, org_id, workflow_run_id), "task_id": str(task_id)}

    result = await talent_analysis_node(state)

    assert result == {"completed_task_ids": [str(task_id)]}
    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(workflow_run_id, f"talent_analysis:{workflow_run_id}")
        roles = await uow.talent.list_role_requirements_for_tender(tender_id)
    assert task is not None
    assert task.status == "completed"
    assert task.output_refs["role_count"] == 1
    assert task.output_refs["candidate_count"] == 1
    assert task.output_refs["evaluated_count"] == 1
    assert len(roles) == 1
    assert roles[0].role_name == "Project Manager"

    async with UnitOfWork() as uow:
        matches = await uow.talent.list_role_matches_for_role(roles[0].id)
    assert len(matches) == 1
    assert matches[0].employee_knowledge_item_id == candidate_item_id
    assert matches[0].score == 0.75
    assert matches[0].human_selected is False
