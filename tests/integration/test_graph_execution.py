import pytest
from _helpers import make_ready_run_inputs
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_service, workflow_service
from tenderos_backend.orchestration.nodes import (
    commercial,
    compliance,
    proposal,
    redteam,
    requirement_extraction,
    talent,
)
from tenderos_worker.jobs.workflow import resume_workflow, run_workflow

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _fake_extraction_llm(monkeypatch):
    # Every test in this file drives the real graph through
    # requirement_extraction_node - autouse so no test forgets to patch it
    # out, matching the "every automated test runs against FakeLLMClient,
    # never a live model" rule (agents/llm/fake_client.py's own docstring).
    # Scripted with 5 identical empty responses: this file's own fixture
    # document produces one chunk/one batch normally, but a test isn't
    # guaranteed to stay at exactly one call forever, and a clear
    # AssertionError from an exhausted script is a better failure than a
    # silent hang if that ever changes.
    fake_client = FakeLLMClient()
    fake_client.script(
        "requirement_extraction",
        ExtractionOutput,
        [ExtractionOutput(requirements=[], submission_items=[], clarifications=[])] * 5,
    )
    monkeypatch.setattr(requirement_extraction, "_build_llm_client", lambda: fake_client)
    # compliance_analysis_node constructs its own LLM client unconditionally
    # (before checking whether there are any requirements to evaluate) - with
    # zero extracted requirements the fake client is never actually called,
    # but patching it here keeps this file's real-graph-execution tests from
    # ever depending on a real GEMINI_API_KEY being present.
    monkeypatch.setattr(compliance, "_build_llm_client", lambda: FakeLLMClient())
    # talent_analysis_node also constructs its own client unconditionally
    # (before checking whether any staffing requirements/candidates exist)
    # - same reasoning as compliance's own patch above.
    monkeypatch.setattr(talent, "_build_llm_client", lambda: FakeLLMClient())
    # commercial_analysis_node too - this file's own fixture tender has no
    # uploaded .xlsx pricing documents, so evaluate_commercial_for_tender
    # never actually calls the LLM in practice, but the client is still
    # constructed unconditionally before that check runs.
    monkeypatch.setattr(commercial, "_build_llm_client", lambda: FakeLLMClient())
    # proposal_drafting_node also constructs its own client unconditionally.
    # This file's fixture tender has no approved compliance/talent/commercial
    # data, so draft_proposal_for_tender skips every data-gated section
    # template and calls draft_section exactly once, for "Company Overview"
    # (the one always_on template) - one scripted response is enough, with a
    # few spares for the same "don't silently hang on an exhausted queue"
    # reasoning the extraction fixture above already uses.
    proposal_fake_client = FakeLLMClient()
    proposal_fake_client.script(
        "proposal_drafting",
        ProposalSectionDraft,
        [ProposalSectionDraft(content="Drafted overview.", claims=[])] * 3,
    )
    monkeypatch.setattr(proposal, "_build_llm_client", lambda: proposal_fake_client)
    # red_team_node also constructs its own client unconditionally, same
    # reasoning as compliance/talent/commercial above - B11's new fixed
    # step in this graph (project_graph -> red_team), so every real-graph
    # test in this file now reaches it regardless of what data exists.
    redteam_fake_client = FakeLLMClient()
    redteam_fake_client.script(
        "redteam_critique", RedteamCritique, [RedteamCritique(findings=[])] * 3
    )
    monkeypatch.setattr(redteam, "_build_llm_client", lambda: redteam_fake_client)
    return fake_client


async def _make_run():
    registered, tender = await make_ready_run_inputs(
        "graph-exec-test@example.com", "Graph Exec Test Org", "RFP Graph Exec Test"
    )
    run = await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )
    return registered, run


async def _approve(registered, gate):
    return await gate_service.record_decision(
        gate_id=gate.id,
        organization_id=registered.organization.id,
        decided_by=registered.user.id,
        decision="approve",
        notes=None,
    )


async def test_full_smoke_path_start_to_complete():
    """B3's own smoke test, extended in B7, B8A, and B8B: start ->
    requirement extraction -> interrupt -> approve requirement_review ->
    resume -> parallel specialist branches (compliance, talent, and
    commercial all real now) -> specialist_join -> compliance_review gate ->
    approve -> resume -> resource_selection gate -> approve -> resume ->
    commercial_approval gate -> approve -> resume -> final gate -> approve
    -> complete.

    Each run_workflow/resume_workflow call opens its own get_checkpointer()
    connection independently - nothing is cached between them - which is
    exactly what makes this equivalent to "stop worker, restart worker"
    rather than one continuous in-memory call, and is the closest thing this
    automated suite has to B7's own stop-gate bullet "vertical slice
    survives worker restart" (the real Docker kill-and-restart exercise
    covers the same property against the live stack, not just in-process).
    """
    registered, run = await _make_run()

    await run_workflow(str(run.id))

    async with UnitOfWork() as uow:
        after_first_pause = await uow.workflow.get_run(run.id)
        gates = await uow.gates.list_for_run(run.id)
    assert after_first_pause is not None
    assert after_first_pause.status == "waiting_for_human"
    assert [g.gate_type for g in gates] == ["requirement_review"]

    _, is_new = await _approve(registered, gates[0])
    assert is_new is True
    await resume_workflow(str(run.id), str(gates[0].id))

    async with UnitOfWork() as uow:
        after_specialists = await uow.workflow.get_run(run.id)
        tasks = await uow.workflow.list_tasks(run.id)
        gates = await uow.gates.list_for_run(run.id)

    assert after_specialists is not None
    assert after_specialists.status == "waiting_for_human"
    specialist_types = {"compliance_analysis", "talent_analysis", "commercial_validation"}
    specialist_tasks = [t for t in tasks if t.task_type in specialist_types]
    assert len(specialist_tasks) == 3
    assert all(t.status == "completed" for t in specialist_tasks), (
        "parallel state reducers must not lose any of the 3 branch completions"
    )
    # specialist_join is followed by three sequential gates now (compliance_
    # review, resource_selection, commercial_approval) before final_
    # readiness - the whole graph superstep pauses on compliance's interrupt
    # first, same as before.
    assert {g.gate_type for g in gates} == {"requirement_review", "compliance_review"}
    compliance_gate_row = next(g for g in gates if g.gate_type == "compliance_review")

    await _approve(registered, compliance_gate_row)
    await resume_workflow(str(run.id), str(compliance_gate_row.id))

    async with UnitOfWork() as uow:
        after_compliance_gate = await uow.workflow.get_run(run.id)
        gates = await uow.gates.list_for_run(run.id)

    assert after_compliance_gate is not None
    assert after_compliance_gate.status == "waiting_for_human"
    assert {g.gate_type for g in gates} == {
        "requirement_review",
        "compliance_review",
        "resource_selection",
    }
    resource_selection_gate_row = next(g for g in gates if g.gate_type == "resource_selection")

    await _approve(registered, resource_selection_gate_row)
    await resume_workflow(str(run.id), str(resource_selection_gate_row.id))

    async with UnitOfWork() as uow:
        after_resource_selection_gate = await uow.workflow.get_run(run.id)
        gates = await uow.gates.list_for_run(run.id)

    assert after_resource_selection_gate is not None
    assert after_resource_selection_gate.status == "waiting_for_human"
    assert {g.gate_type for g in gates} == {
        "requirement_review",
        "compliance_review",
        "resource_selection",
        "commercial_approval",
    }
    commercial_approval_gate_row = next(g for g in gates if g.gate_type == "commercial_approval")

    await _approve(registered, commercial_approval_gate_row)
    await resume_workflow(str(run.id), str(commercial_approval_gate_row.id))

    async with UnitOfWork() as uow:
        after_commercial_approval_gate = await uow.workflow.get_run(run.id)
        gates = await uow.gates.list_for_run(run.id)

    assert after_commercial_approval_gate is not None
    assert after_commercial_approval_gate.status == "waiting_for_human"
    assert {g.gate_type for g in gates} == {
        "requirement_review",
        "compliance_review",
        "resource_selection",
        "commercial_approval",
        "final_readiness",
    }
    final_gate_row = next(g for g in gates if g.gate_type == "final_readiness")

    await _approve(registered, final_gate_row)
    await resume_workflow(str(run.id), str(final_gate_row.id))

    async with UnitOfWork() as uow:
        completed_run = await uow.workflow.get_run(run.id)
    assert completed_run is not None
    assert completed_run.status == "completed"


async def test_repeating_same_gate_decision_is_idempotent():
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
    gate = gates[0]

    first_gate, first_is_new = await _approve(registered, gate)
    second_gate, second_is_new = await _approve(registered, gate)

    assert first_is_new is True
    assert second_is_new is False
    assert first_gate.decided_at == second_gate.decided_at

    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == gate.id)
        )
    assert len(result.scalars().all()) == 1, "a replayed decision must not double-enqueue a resume"


async def test_resume_continues_the_same_thread_not_a_new_workflow():
    """The literal property R1/B3's do-not-proceed criterion names: resuming
    must continue the exact checkpointed thread, not start a fresh run. If
    resume_workflow accidentally passed a plain initial TenderState instead of
    Command(resume=...), this would silently re-run load_context/supervisor
    and create a SECOND requirement_extraction task instead of proceeding past
    the gate - this test would catch that regression.
    """
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        tasks_before = await uow.workflow.list_tasks(run.id)
    # supervisor_node (which runs before requirement_gate) already creates all
    # 4 task rows in this first pass - row count never changes across resume,
    # only status does, so "forward progress" has to be measured by status
    # transitioning, not by counting rows.
    specialist_types = {"compliance_analysis", "talent_analysis", "commercial_validation"}
    specialists_before = [t for t in tasks_before if t.task_type in specialist_types]
    assert all(t.status == "pending" for t in specialists_before)

    await _approve(registered, gates[0])
    await resume_workflow(str(run.id), str(gates[0].id))

    async with UnitOfWork() as uow:
        tasks_after = await uow.workflow.list_tasks(run.id)
    requirement_tasks_after = [t for t in tasks_after if t.task_type == "requirement_extraction"]
    assert len(requirement_tasks_after) == 1, "resume must not restart the graph from load_context"
    assert len(tasks_after) == len(tasks_before), "resume must not create duplicate task rows"
    specialists_after = [t for t in tasks_after if t.task_type in specialist_types]
    assert all(t.status == "completed" for t in specialists_after), (
        "resume must make forward progress, not stall"
    )


async def test_run_workflow_does_nothing_for_an_already_cancelled_run():
    registered, run = await _make_run()
    await workflow_service.cancel_workflow_run(
        run.id, registered.organization.id, registered.user.id
    )

    await run_workflow(str(run.id))

    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        tasks = await uow.workflow.list_tasks(run.id)
        unchanged_run = await uow.workflow.get_run(run.id)
    assert gates == []
    assert tasks == []
    assert unchanged_run is not None
    assert unchanged_run.status == "cancelled"


async def test_resume_workflow_does_nothing_once_cancelled_mid_flight():
    """Cancel a run that's already paused at requirement_review, then try to
    approve+resume it anyway - I6's actual scenario: cancellation must win
    even though a decision was already recorded on the gate."""
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
    gate = gates[0]

    await workflow_service.cancel_workflow_run(
        run.id, registered.organization.id, registered.user.id
    )
    await _approve(registered, gate)
    await resume_workflow(str(run.id), str(gate.id))

    async with UnitOfWork() as uow:
        tasks = await uow.workflow.list_tasks(run.id)
        still_cancelled_run = await uow.workflow.get_run(run.id)
    specialist_types = {"compliance_analysis", "talent_analysis", "commercial_validation"}
    specialist_tasks = [t for t in tasks if t.task_type in specialist_types]
    assert all(t.status == "pending" for t in specialist_tasks), (
        "cancellation must block dispatch even after a gate was approved"
    )
    assert still_cancelled_run is not None
    assert still_cancelled_run.status == "cancelled"
