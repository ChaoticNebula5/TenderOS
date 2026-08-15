"""commercial_analysis_node against the real synthetic pricing workbooks and
a scripted FakeLLMClient - task completion and every finding persisted end
to end through the real graph node, not just the underlying service."""

import uuid
from pathlib import Path

import pytest
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service
from tenderos_backend.orchestration.nodes import commercial
from tenderos_backend.orchestration.nodes.commercial import commercial_analysis_node

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


async def _upload_pricing_sheet(org_id: uuid.UUID, tender_id: uuid.UUID, filename: str) -> None:
    content = (FIXTURES_DIR / filename).read_bytes()
    await document_service.create_document(
        tender_id=tender_id,
        organization_id=org_id,
        actor_user_id=uuid.uuid4(),
        filename=filename,
        content=content,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


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


async def _make_commercial_task(workflow_run_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=workflow_run_id,
            task_key=f"commercial_analysis:{workflow_run_id}",
            task_type="commercial_validation",
            assigned_agent="commercial",
            priority=5,
            input_refs={},
        )
        await uow.commit()
        return task.id


async def test_node_evaluates_both_pricing_sheets_and_completes_task(monkeypatch):
    org_id, tender_id = await _make_org_and_tender("Commercial Node Test")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_1.xlsx")
    await _upload_pricing_sheet(org_id, tender_id, "pricing_schedule_2.xlsx")
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    fake_client.script(
        "commercial_explanation",
        FindingExplanation,
        [FindingExplanation(severity="major", explanation="Deterministically identified.")] * 8,
    )
    monkeypatch.setattr(commercial, "_build_llm_client", lambda: fake_client)

    task_id = await _make_commercial_task(workflow_run_id)
    state = {**_base_state(tender_id, org_id, workflow_run_id), "task_id": str(task_id)}

    result = await commercial_analysis_node(state)

    assert result == {"completed_task_ids": [str(task_id)]}
    async with UnitOfWork() as uow:
        task = await uow.workflow.get_task_by_key(
            workflow_run_id, f"commercial_analysis:{workflow_run_id}"
        )
        findings = await uow.commercial.list_pricing_findings_for_tender(tender_id)
    assert task is not None
    assert task.status == "completed"
    assert task.output_refs["finding_count"] == 8
    assert len(findings) == 8
    assert {f.finding_type for f in findings} == {
        "wrong_total",
        "duplicate_cost",
        "currency_mismatch",
        "needs_human_review",
        "missing_cell",
        "cross_doc_mismatch",
    }
