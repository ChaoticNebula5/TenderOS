"""proposal_drafting_node against real Postgres and a scripted
FakeLLMClient - node-level wiring end to end (status write + real
persisted section), not a re-test of proposal_service.py's own grounded-
context/claim-invariant logic (covered directly in test_proposal_service.py
and test_claim_grounding.py)."""

import uuid

import pytest
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.orchestration.nodes import proposal
from tenderos_backend.orchestration.nodes.proposal import proposal_drafting_node

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
        "degraded_stages": [],
    }


async def _make_workflow_run_row(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()
        return run.id


async def test_node_drafts_company_overview_and_sets_drafting_status(monkeypatch):
    org_id, tender_id = await _make_org_and_tender("Proposal Node Test")
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    fake_client.script(
        "proposal_drafting",
        ProposalSectionDraft,
        [ProposalSectionDraft(content="Drafted overview.", claims=[])],
    )
    monkeypatch.setattr(proposal, "_build_llm_client", lambda: fake_client)

    result = await proposal_drafting_node(_base_state(tender_id, org_id, workflow_run_id))

    assert result == {}
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(workflow_run_id)
        sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
    assert run is not None
    assert run.status == "running", "drafting maps to the coarse 'running' status, not waiting_for_human"
    assert [s.section_name for s in sections] == ["Company Overview"]
    assert sections[0].content == "Drafted overview."


async def test_node_swallows_a_drafting_failure_rather_than_raising(monkeypatch):
    org_id, tender_id = await _make_org_and_tender("Proposal Node Failure Test")
    workflow_run_id = await _make_workflow_run_row(tender_id)

    fake_client = FakeLLMClient()
    # No script registered at all - draft_section's call_with_repair will
    # raise (via FakeLLMClient's own "no scripted response left" guard),
    # simulating a real drafting failure.
    monkeypatch.setattr(proposal, "_build_llm_client", lambda: fake_client)

    result = await proposal_drafting_node(_base_state(tender_id, org_id, workflow_run_id))

    assert not result.get(
        "status"
    ), "a drafting failure must not propagate - no workflow_tasks row exists for this node to fail against"
    # Regression test for the audit's finding #16: this failure used to be
    # invisible outside the logs - a run could reach "ready" with zero
    # proposal sections and nothing in run state said so. degraded_stages
    # is how final_gate_node's own gate summary now surfaces it.
    assert result == {"degraded_stages": ["proposal_drafting:AssertionError"]}, result
    async with UnitOfWork() as uow:
        sections = await uow.proposal.list_proposal_sections_for_tender(tender_id)
    assert sections == []
