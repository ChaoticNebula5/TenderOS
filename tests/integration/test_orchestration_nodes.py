import pytest
from _helpers import make_ready_run_inputs
from langgraph.types import Send
from sqlalchemy import select
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import workflow_service
from tenderos_backend.orchestration.nodes import final_gate, requirement
from tenderos_backend.orchestration.nodes.requirement import (
    commercial_stub_node,
    compliance_stub_node,
    talent_stub_node,
)
from tenderos_backend.orchestration.nodes.specialist_dispatch import (
    _SPECIALIST_NODES,
    dispatch_specialists,
)

pytestmark = pytest.mark.asyncio


async def _make_run() -> WorkflowRun:
    registered, tender = await make_ready_run_inputs(
        "node-test@example.com", "Node Test Org", "RFP Node Test"
    )
    return await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )


def _base_state(run: WorkflowRun) -> dict:
    return {
        "tender_id": str(run.tender_id),
        "organization_id": "unused-by-these-nodes",
        "workflow_run_id": str(run.id),
        "langgraph_thread_id": run.langgraph_thread_id,
        "status": "planning",
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


# requirement_extraction_node (replacing B3's requirement_stub_node) makes a
# real LLM call and needs real chunks to run against - its task-creation/
# no-duplicate-on-rerun coverage now lives in
# tests/integration/test_requirement_extraction_node.py, built against a
# FakeLLMClient and real synthetic chunks rather than bare node-state dicts.


@pytest.mark.parametrize("node", [compliance_stub_node, talent_stub_node, commercial_stub_node])
async def test_specialist_stub_completes_only_its_own_task(node):
    run = await _make_run()
    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key=f"{node.__name__}:{run.id}",
            task_type="compliance_analysis",
            assigned_agent="stub",
            priority=5,
            input_refs={},
        )
        await uow.commit()

    state = {**_base_state(run), "task_id": str(task.id)}
    result = await node(state)

    assert result == {"completed_task_ids": [str(task.id)]}
    async with UnitOfWork() as uow:
        completed = await uow.workflow.get_task_by_key(run.id, task.task_key)
    assert completed is not None
    assert completed.status == "completed"
    assert "summary" in completed.output_refs


# requirement_gate_node/final_gate_node call interrupt() at the very end -
# outside of a real compiled+invoked graph, interrupt() has no LangGraph
# execution context to pause, so it can't be exercised end-to-end here (that
# needs TODO #4's real checkpointer, covered separately). Monkeypatching
# interrupt() to return a fixed decision immediately lets these tests verify
# the one thing that IS pure node logic, independent of LangGraph's runtime:
# the pre-interrupt gate-opening step must be idempotent (R3's "any side
# effect before an interrupt is idempotent" rule, and B3's own required test
# "side effects before interrupt are not duplicated").


@pytest.mark.parametrize(
    ("module", "node", "gate_type"),
    [
        (requirement, requirement.requirement_gate_node, "requirement_review"),
        (final_gate, final_gate.final_gate_node, "final_readiness"),
    ],
)
async def test_gate_node_opens_exactly_one_gate_even_when_rerun(
    module, node, gate_type, monkeypatch
):
    monkeypatch.setattr(module, "interrupt", lambda payload: {"decision": "approve", "notes": None})
    run = await _make_run()
    state = _base_state(run)

    first = await node(state)
    second = await node(state)

    assert first == {
        "status": "running_specialists" if gate_type == "requirement_review" else "ready"
    }
    assert first == second
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(ApprovalGate).where(
                ApprovalGate.workflow_run_id == run.id,
                ApprovalGate.gate_type == gate_type,
            )
        )
    assert len(result.scalars().all()) == 1


async def test_requirement_gate_node_rejection_routes_to_rejected(monkeypatch):
    monkeypatch.setattr(
        requirement, "interrupt", lambda payload: {"decision": "reject", "notes": "not ready"}
    )
    run = await _make_run()

    result = await requirement.requirement_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    async with UnitOfWork() as uow:
        updated_run = await uow.workflow.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == "failed"


async def test_dispatch_specialists_sends_one_per_specialist_node_in_order():
    run = await _make_run()
    state = {**_base_state(run), "dispatch_task_ids": ["task-a", "task-b", "task-c"]}

    sends = await dispatch_specialists(state)

    assert len(sends) == 3
    assert all(isinstance(s, Send) for s in sends)
    assert [s.node for s in sends] == list(_SPECIALIST_NODES)
    assert [s.arg["task_id"] for s in sends] == ["task-a", "task-b", "task-c"]


async def test_dispatch_specialists_preserves_shared_state_in_each_payload():
    run = await _make_run()
    state = {
        **_base_state(run),
        "status": "running_specialists",
        "dispatch_task_ids": ["task-a", "task-b", "task-c"],
    }

    sends = await dispatch_specialists(state)

    for send in sends:
        assert send.arg["status"] == "running_specialists"


# I6 cancellation enforcement - three independent checks (job-wrapper level is
# covered separately in test_graph_execution.py, since that needs
# run_workflow/resume_workflow, not bare node calls).


async def _cancel(run: WorkflowRun) -> None:
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get(run.tender_id)
    assert tender is not None
    assert tender.created_by is not None
    await workflow_service.cancel_workflow_run(run.id, tender.organization_id, tender.created_by)


async def test_dispatch_specialists_sends_nothing_for_a_cancelled_run():
    run = await _make_run()
    await _cancel(run)
    state = {**_base_state(run), "dispatch_task_ids": ["task-a", "task-b", "task-c"]}

    sends = await dispatch_specialists(state)

    assert sends == []


async def test_requirement_gate_node_opens_no_gate_for_a_cancelled_run(monkeypatch):
    interrupt_called = False

    def _fail_if_called(payload):
        nonlocal interrupt_called
        interrupt_called = True
        return {"decision": "approve", "notes": None}

    monkeypatch.setattr(requirement, "interrupt", _fail_if_called)
    run = await _make_run()
    await _cancel(run)

    result = await requirement.requirement_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    assert interrupt_called is False, "a cancelled run must not open interrupt() at all"
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        updated_run = await uow.workflow.get_run(run.id)
    assert gates == []
    assert updated_run is not None
    assert updated_run.status == "cancelled", "must not be overwritten to 'failed'"
