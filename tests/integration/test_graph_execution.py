import asyncio

import pytest
from _helpers import make_ready_run_inputs
from sqlalchemy import select
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.agents.schemas.requirements import ExtractionOutput
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_service, workflow_service
from tenderos_backend.orchestration.checkpoint import get_checkpointer
from tenderos_backend.orchestration.graph import build_graph
from tenderos_backend.orchestration.nodes import (
    commercial,
    compliance,
    proposal,
    redteam,
    requirement_extraction,
    talent,
)
from tenderos_worker.jobs.workflow import resume_workflow, retry_workflow_run, run_workflow

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


async def test_one_failing_specialist_branch_cancels_its_still_running_siblings(monkeypatch):
    """The sibling-branch-abort question (KNOWN_LIMITATIONS.md's major
    finding #17 entry, trigger event 2 - "may still be capable of
    aborting sibling branches that never got a chance to run... not yet
    independently confirmed"), answered directly rather than left open.

    Expected, stated before running, from reading LangGraph 1.2.10's own
    installed source (.venv/Lib/site-packages/langgraph/pregel/_runner.py)
    rather than assumed: compliance_analysis/talent_analysis/
    commercial_analysis are three tasks Send()-dispatched into the SAME
    Pregel super-step (graph.py's own comment: "all Send()-dispatched...
    same shape for all three branches"). PregelRunner.tick's async loop
    waits on asyncio.wait(futures, return_when=FIRST_COMPLETED) in a loop;
    the moment a done future carries an exception that is not a
    GraphBubbleUp (an interrupt) and has no registered per-node error
    handler (this graph registers none - no retry_policy/error handler is
    passed to any add_node call in graph.py), _should_stop_others - whose
    own docstring says exactly "Check if any task failed, if so, cancel
    all other tasks" - returns True, the wait loop breaks, and every
    future still in `futures` (the still in-flight siblings) gets
    `.cancel()`'d before the panic/reraise.

    First run of this test (kept honest, not smoothed over): with NO
    artificial delay, compliance_analysis/talent_analysis actually WON the
    race and completed before commercial_analysis's near-instant,
    zero-await failure ever got detected - this fixture tender has no
    requirements/documents, so both siblings' own per-item loops run zero
    iterations and finish essentially synchronously. That is a real,
    important difference from B16's own live-run trigger, where compliance_
    analysis was mid-flight through dozens of real per-requirement Gemini
    calls (genuine network latency) when commercial's RateLimitedError hit
    - the failure and the siblings were racing over real wall-clock time
    there, not over near-zero local work. A `_SlowUnitOfWork` wrapper
    (below) reproduces that same real-latency shape deterministically,
    without needing a live LLM call: it inserts one genuine, cancellable
    await (asyncio.sleep) at the very first line of each sibling's real
    body, so they are provably still in-flight when commercial's
    zero-await raise fires.

    That first run also surfaced a second, more severe finding before this
    fix: a cancelled sibling's own asyncpg connection is left in a broken
    state, and with no `pool_pre_ping` (db/session.py, now fixed), the
    pool silently handed that broken connection to the very next,
    unrelated query, which failed with a raw `InterfaceError` unrelated to
    anything it was actually doing. See db/session.py's own comment.

    Expected now: commercial_analysis fails and marks itself 'failed' via
    its own except-block (compliance.py/commercial.py's identical `except
    Exception as exc: mark_task_failed(...); raise` pattern); compliance_
    analysis/talent_analysis get asyncio-cancelled by LangGraph mid-sleep -
    and since asyncio.CancelledError is a BaseException, not an Exception,
    their OWN `except Exception:` blocks never fire either, so
    mark_task_failed never runs for them either. Also checked directly,
    not assumed: no code anywhere in this codebase ever sets
    WorkflowTask.status='running' or .started_at (grepped - only
    create_task/mark_task_completed/mark_task_failed exist) - so a
    cancelled-mid-flight task and a task that never got scheduled at all
    are indistinguishable from the row alone, both simply 'pending'. That
    is itself the reason B16's original live-run observation ("both at
    status='pending' with no started_at") could not tell "never ran" from
    "ran, then cancelled" apart.
    """
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
    await _approve(registered, gates[0])

    call_count = {"n": 0}

    def _commercial_client_fails_once_then_succeeds():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("scripted commercial failure")
        return FakeLLMClient()

    monkeypatch.setattr(
        commercial, "_build_llm_client", _commercial_client_fails_once_then_succeeds
    )

    class _SlowUnitOfWork(UnitOfWork):
        # A real, cancellable await at the very first line of the real
        # node body - resolved via compliance.py's/talent.py's own module
        # globals (each does `from tenderos_backend.db.unit_of_work import
        # UnitOfWork`), so patching the name on those two modules reaches
        # every `async with UnitOfWork() as uow:` inside them without
        # touching commercial.py or any other caller.
        async def __aenter__(self):
            await asyncio.sleep(0.5)
            return await super().__aenter__()

    monkeypatch.setattr(compliance, "UnitOfWork", _SlowUnitOfWork)
    monkeypatch.setattr(talent, "UnitOfWork", _SlowUnitOfWork)

    with pytest.raises(RuntimeError, match="scripted commercial failure"):
        await resume_workflow(str(run.id), str(gates[0].id))

    async with UnitOfWork() as uow:
        tasks = await uow.workflow.list_tasks(run.id)
    by_type = {t.task_type: t for t in tasks}

    commercial_task = by_type["commercial_validation"]
    assert commercial_task.status == "failed"
    assert commercial_task.error_code == "RuntimeError"

    compliance_task = by_type["compliance_analysis"]
    talent_task = by_type["talent_analysis"]
    assert compliance_task.status == "pending", (
        "confirmed: LangGraph's own _should_stop_others cancelled this "
        "still-in-flight sibling before it ever reached mark_task_completed"
    )
    assert talent_task.status == "pending"
    assert compliance_task.completed_at is None
    assert talent_task.completed_at is None
    assert compliance_task.error_code is None, (
        "a cancelled task's own except-block never ran - CancelledError is "
        "a BaseException, not an Exception, so mark_task_failed was never "
        "called for it either"
    )

    # Group 4's retry mechanism (major finding #17), resuming the exact
    # SAME LangGraph checkpoint thread - does it correctly re-dispatch the
    # cancelled siblings too, or only the one task that actually raised?
    # Bypasses the real RQ scheduling delay (classify_task_failure's own
    # 15s fixed backoff for an unclassified exception) by calling the
    # scheduled job's own target function directly, matching
    # test_workflow_retry.py's own established "don't wait for RQ, call
    # the target directly" convention.
    await retry_workflow_run(str(run.id))

    async with UnitOfWork() as uow:
        tasks_after_retry = await uow.workflow.list_tasks(run.id)
    by_type_after = {t.task_type: t for t in tasks_after_retry}
    assert by_type_after["commercial_validation"].status == "completed"
    assert by_type_after["compliance_analysis"].status == "completed", (
        "this is the sibling-branch-abort question's real answer: resuming "
        "the same checkpoint after a retryable failure re-executes the "
        "WHOLE aborted super-step, not just the one task that raised - "
        "Group 4's retry work does close this, at least for a run that "
        "hasn't paused/checkpointed past this super-step yet"
    )
    assert by_type_after["talent_analysis"].status == "completed"


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


async def test_under_budget_run_opens_no_cost_budget_gate(monkeypatch):
    """I5's gate (Build Guide :2755-2763) - the common path. FakeLLMClient
    calls never write a real estimated_cost_usd (no real Gemini response,
    no real usage_metadata), so accumulated cost reads as $0 regardless of
    settings.max_tender_cost_usd's own default - proves the gate stays a
    true no-op when nothing is actually spent, not just when explicitly
    configured generously.
    """
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
    await _approve(registered, gates[0])
    await resume_workflow(str(run.id), str(gates[0].id))

    async with UnitOfWork() as uow:
        gates_after = await uow.gates.list_for_run(run.id)
        tasks_after = await uow.workflow.list_tasks(run.id)
    assert not any(g.gate_type == "cost_budget" for g in gates_after)
    specialist_types = {"compliance_analysis", "talent_analysis", "commercial_validation"}
    specialist_tasks = [t for t in tasks_after if t.task_type in specialist_types]
    assert all(t.status == "completed" for t in specialist_tasks)


async def test_over_budget_run_opens_a_gate_and_approving_it_unblocks_specialists(monkeypatch):
    """The real point of I5: a run that's already over budget pauses BEFORE
    specialist_dispatch fires (no specialist task leaves 'pending'), shows
    real progress in the gate's own summary, and only proceeds once a real
    approval decision is recorded - not silently, not on a timer.
    """
    monkeypatch.setattr(settings, "max_tender_cost_usd", 0.0)

    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
    await _approve(registered, gates[0])
    await resume_workflow(str(run.id), str(gates[0].id))

    async with UnitOfWork() as uow:
        gates_after_first_resume = await uow.gates.list_for_run(run.id)
        tasks_still_pending = await uow.workflow.list_tasks(run.id)
        run_after_first_resume = await uow.workflow.get_run(run.id)
    cost_budget_gate = next(
        g for g in gates_after_first_resume if g.gate_type == "cost_budget"
    )
    assert cost_budget_gate.status == "pending"
    assert cost_budget_gate.request_payload["summary"]["accumulated_cost_usd"] == 0.0
    assert cost_budget_gate.request_payload["summary"]["budget_usd"] == 0.0
    specialist_types = {"compliance_analysis", "talent_analysis", "commercial_validation"}
    specialist_tasks = [t for t in tasks_still_pending if t.task_type in specialist_types]
    assert all(t.status == "pending" for t in specialist_tasks), (
        "no specialist task may start before the cost_budget gate is decided"
    )
    assert run_after_first_resume is not None
    assert run_after_first_resume.status == "waiting_for_human"

    await _approve(registered, cost_budget_gate)
    await resume_workflow(str(run.id), str(cost_budget_gate.id))

    async with UnitOfWork() as uow:
        tasks_after_approval = await uow.workflow.list_tasks(run.id)
        run_after_approval = await uow.workflow.get_run(run.id)
    specialist_tasks_after = [
        t for t in tasks_after_approval if t.task_type in specialist_types
    ]
    assert all(t.status == "completed" for t in specialist_tasks_after), (
        "approving the cost_budget gate must let specialists actually run, "
        "not leave them stranded"
    )
    assert run_after_approval is not None
    assert run_after_approval.status == "waiting_for_human", (
        "workflow_runs.status must reflect the real next pause "
        "(compliance_review), not stay stuck on the cost_budget gate's own "
        "'running_specialists' write forever"
    )


# ---------------------------------------------------------------------------
# Phase 4 consumer guard: resume_workflow only resumes the interrupt for the
# decided gate, and only while the run is waiting for it.
# ---------------------------------------------------------------------------


async def _pending_gate_ids(run) -> list[str]:
    async with get_checkpointer() as checkpointer:
        snapshot = await build_graph(checkpointer).aget_state(
            {"configurable": {"thread_id": run.langgraph_thread_id}}
        )
    return [i.value["gate_id"] for i in snapshot.interrupts if isinstance(i.value, dict)]


async def _run_to_the_second_gate():
    """requirement_review (gate A) approved and resumed; the thread then
    pauses at compliance_review (gate B)."""
    registered, run = await _make_run()
    await run_workflow(str(run.id))
    async with UnitOfWork() as uow:
        gate_a = (await uow.gates.list_for_run(run.id))[0]
    assert gate_a.gate_type == "requirement_review"
    await _approve(registered, gate_a)
    await resume_workflow(str(run.id), str(gate_a.id))

    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        after = await uow.workflow.get_run(run.id)
    gate_b = next(g for g in gates if g.gate_type == "compliance_review")
    assert after is not None and after.status == "waiting_for_human"
    assert await _pending_gate_ids(run) == [str(gate_b.id)]
    return registered, run, gate_a, gate_b


async def test_a_duplicate_resume_for_an_earlier_gate_does_not_decide_the_next_one():
    """Before the guard, gate A's duplicate Command(resume=payload) carried
    no interrupt id, so gate B's interrupt() took it as its own answer: gate
    B was decided with gate A's payload and the run moved on to
    resource_selection. Now the duplicate finds no pending interrupt for
    gate A and does nothing: gate B stays pending, the run stays
    waiting_for_human, no new gate appears."""
    registered, run, gate_a, gate_b = await _run_to_the_second_gate()
    async with UnitOfWork() as uow:
        gate_types_before = sorted(g.gate_type for g in await uow.gates.list_for_run(run.id))

    await resume_workflow(str(run.id), str(gate_a.id))

    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        after = await uow.workflow.get_run(run.id)
        gate_b_after = await uow.gates.get(gate_b.id)
    assert sorted(g.gate_type for g in gates) == gate_types_before
    assert gate_b_after is not None
    assert gate_b_after.decision_payload is None
    assert after is not None and after.status == "waiting_for_human"
    assert await _pending_gate_ids(run) == [str(gate_b.id)]


async def test_the_genuine_resume_for_the_waiting_gate_still_works():
    """The guard must not block the real resume: after the duplicate above
    is ignored, deciding gate B and resuming it moves the run on to the
    next gate, as before."""
    registered, run, gate_a, gate_b = await _run_to_the_second_gate()
    await resume_workflow(str(run.id), str(gate_a.id))  # the ignored duplicate

    await _approve(registered, gate_b)
    await resume_workflow(str(run.id), str(gate_b.id))

    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        after = await uow.workflow.get_run(run.id)
    assert {g.gate_type for g in gates} == {
        "requirement_review",
        "compliance_review",
        "resource_selection",
    }
    resource_gate = next(g for g in gates if g.gate_type == "resource_selection")
    assert after is not None and after.status == "waiting_for_human"
    assert await _pending_gate_ids(run) == [str(resource_gate.id)]
