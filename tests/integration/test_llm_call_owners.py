"""PR 29 (Q1 b): who owns an LLM call's agent_traces row.

A row is owned by at most one of a workflow run, an async operation or a
reanalysis claim (ck_agent_trace_one_owner, migration 037); on-demand
verification and knowledge imports own none. Before migration 037 the three
operation kinds wrote their calls with no owner at all, so neither cost per
run nor anything else counted them.

Here: each operation kind's calls carry its operation, through the real
handlers in jobs/operations.py with a scripted FakeLLMClient; the CHECK and
the repository refuse a row with two owners; thinking_tokens cannot be
negative.
"""

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from _helpers import register_org_and_tender
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.models.agent_trace import AgentTrace
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import document_service, operation_service
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs.operations import run_operation

pytestmark = pytest.mark.asyncio

_PRICING_FIXTURES = (
    Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    # The red-team operation reads the graph; the same cleanup as
    # test_async_operations.py.
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _request(email: str, kind: str) -> operation_service.OperationRequest:
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    return await operation_service.request_operation(
        registered.organization.id, tender.id, kind, registered.user.id
    )


async def _owners(tender_id: uuid.UUID) -> list[tuple]:
    async with get_session() as session:
        rows = await session.execute(
            select(
                AgentTrace.agent_name,
                AgentTrace.operation_id,
                AgentTrace.workflow_run_id,
                AgentTrace.reanalysis_claim_id,
            ).where(AgentTrace.tender_id == tender_id)
        )
        return [tuple(r) for r in rows]


async def test_a_red_team_operations_calls_carry_the_operation(monkeypatch):
    fake = FakeLLMClient()
    fake.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])])
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake)
    op = (await _request("owners-redteam@example.com", "redteam_run")).operation

    await run_operation(str(op.id))

    assert await _owners(op.tender_id) == [("redteam_critique", op.id, None, None)]


async def test_a_proposal_generations_calls_carry_the_operation(monkeypatch):
    fake = FakeLLMClient()
    fake.script(
        "proposal_drafting",
        ProposalSectionDraft,
        [ProposalSectionDraft(content="Fake overview.", claims=[])],
    )
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake)
    op = (await _request("owners-proposal@example.com", "proposal_generation")).operation

    await run_operation(str(op.id))

    assert await _owners(op.tender_id) == [("proposal_drafting", op.id, None, None)]


async def test_a_commercial_validations_calls_carry_the_operation(monkeypatch):
    fake = FakeLLMClient()
    fake.script(
        "commercial_explanation",
        FindingExplanation,
        [FindingExplanation(severity="major", explanation="Fake explanation.")] * 4,
    )
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake)
    op = (await _request("owners-commercial@example.com", "commercial_validation")).operation
    await document_service.create_document(
        tender_id=op.tender_id,
        organization_id=op.organization_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing_schedule_1.xlsx",
        content=(_PRICING_FIXTURES / "pricing_schedule_1.xlsx").read_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    await run_operation(str(op.id))

    assert await _owners(op.tender_id) == [("commercial_explanation", op.id, None, None)] * 4


async def test_a_row_with_two_owners_is_refused_by_the_database():
    op = (await _request("owners-check@example.com", "redteam_run")).operation
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=op.tender_id, prompt_bundle_version="test", model_config={}
        )
        await uow.commit()

    with pytest.raises(IntegrityError, match="ck_agent_trace_one_owner"):
        async with get_session() as session:
            session.add(
                AgentTrace(
                    agent_name="redteam_critique",
                    status="completed",
                    tender_id=op.tender_id,
                    workflow_run_id=run.id,
                    operation_id=op.id,
                )
            )
            await session.commit()


async def test_the_repository_refuses_two_owners_before_the_database():
    op = (await _request("owners-repo@example.com", "redteam_run")).operation

    with pytest.raises(ValueError, match="at most one of"):
        async with UnitOfWork() as uow:
            await uow.agent_traces.create(
                agent_name="redteam_critique",
                status="completed",
                tender_id=op.tender_id,
                operation_id=op.id,
                reanalysis_claim_id=uuid.uuid4(),
            )


async def test_thinking_tokens_cannot_be_negative():
    op = (await _request("owners-thinking@example.com", "redteam_run")).operation

    with pytest.raises(IntegrityError, match="ck_agent_trace_thinking_tokens"):
        async with UnitOfWork() as uow:
            await uow.agent_traces.create(
                agent_name="redteam_critique",
                status="completed",
                tender_id=op.tender_id,
                operation_id=op.id,
                thinking_tokens=-1,
            )
            await uow.commit()
