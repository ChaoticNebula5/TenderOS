import pytest
from sqlalchemy import select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    auth_service,
    gate_service,
    tender_service,
    workflow_service,
)
from tenderos_worker.jobs.workflow import resume_workflow, run_workflow

pytestmark = pytest.mark.asyncio


async def _make_run():
    registered = await auth_service.register(
        email="graph-exec-test@example.com",
        password="hunter2",
        organization_name="Graph Exec Test Org",
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="RFP Graph Exec Test",
        created_by=registered.user.id,
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
    """B3's own smoke test: start -> requirement stub -> interrupt -> approve
    gate -> resume -> parallel specialist stubs -> final gate -> approve ->
    complete. Each run_workflow/resume_workflow call opens its own
    get_checkpointer() connection independently - nothing is cached between
    them - which is exactly what makes this equivalent to "stop worker,
    restart worker" rather than one continuous in-memory call.
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
    assert {g.gate_type for g in gates} == {"requirement_review", "final_readiness"}
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
