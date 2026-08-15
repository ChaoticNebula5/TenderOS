import pytest
from _helpers import make_ready_run_inputs
from langgraph.types import Send
from sqlalchemy import select
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import workflow_service
from tenderos_backend.orchestration.nodes import commercial, compliance, final_gate, requirement, talent
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
# compliance_analysis_node/talent_analysis_node/commercial_analysis_node
# (replacing B3's three stub nodes across B7/B8A/B8B) are the same shape -
# their own coverage lives in test_compliance_analysis_node.py/
# test_talent_analysis_node.py/test_commercial_analysis_node.py for the same
# reason. No specialist stubs remain to test here - all three branches are
# now real.


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
        (compliance, compliance.compliance_gate_node, "compliance_review"),
        (talent, talent.resource_selection_gate_node, "resource_selection"),
        (commercial, commercial.commercial_approval_gate_node, "commercial_approval"),
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

    # compliance_gate_node/resource_selection_gate_node/
    # commercial_approval_gate_node deliberately write no "status" on
    # approve (see compliance_gate_node's own docstring) - the next node in
    # the same un-Send()'d continuation owns that transition, not the gate
    # node itself.
    if gate_type == "final_readiness":
        expected = {"status": "ready"}
    elif gate_type in ("compliance_review", "resource_selection", "commercial_approval"):
        expected = {}
    else:
        expected = {"status": "running_specialists"}
    assert first == expected
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


async def test_compliance_gate_node_rejection_routes_to_rejected(monkeypatch):
    monkeypatch.setattr(
        compliance, "interrupt", lambda payload: {"decision": "reject", "notes": "not ready"}
    )
    run = await _make_run()

    result = await compliance.compliance_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    async with UnitOfWork() as uow:
        updated_run = await uow.workflow.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == "failed"


async def test_resource_selection_gate_node_rejection_routes_to_rejected(monkeypatch):
    monkeypatch.setattr(
        talent, "interrupt", lambda payload: {"decision": "reject", "notes": "not ready"}
    )
    run = await _make_run()

    result = await talent.resource_selection_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    async with UnitOfWork() as uow:
        updated_run = await uow.workflow.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == "failed"


async def test_resource_selection_gate_node_opens_no_gate_for_a_cancelled_run(monkeypatch):
    interrupt_called = False

    def _fail_if_called(payload):
        nonlocal interrupt_called
        interrupt_called = True
        return {"decision": "approve", "notes": None}

    monkeypatch.setattr(talent, "interrupt", _fail_if_called)
    run = await _make_run()
    await _cancel(run)

    result = await talent.resource_selection_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    assert interrupt_called is False, "a cancelled run must not open interrupt() at all"
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        updated_run = await uow.workflow.get_run(run.id)
    assert gates == []
    assert updated_run is not None
    assert updated_run.status == "cancelled", "must not be overwritten to 'failed'"


async def test_commercial_approval_gate_node_rejection_routes_to_rejected(monkeypatch):
    monkeypatch.setattr(
        commercial, "interrupt", lambda payload: {"decision": "reject", "notes": "not ready"}
    )
    run = await _make_run()

    result = await commercial.commercial_approval_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    async with UnitOfWork() as uow:
        updated_run = await uow.workflow.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == "failed"


async def test_commercial_approval_gate_node_opens_no_gate_for_a_cancelled_run(monkeypatch):
    interrupt_called = False

    def _fail_if_called(payload):
        nonlocal interrupt_called
        interrupt_called = True
        return {"decision": "approve", "notes": None}

    monkeypatch.setattr(commercial, "interrupt", _fail_if_called)
    run = await _make_run()
    await _cancel(run)

    result = await commercial.commercial_approval_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    assert interrupt_called is False, "a cancelled run must not open interrupt() at all"
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        updated_run = await uow.workflow.get_run(run.id)
    assert gates == []
    assert updated_run is not None
    assert updated_run.status == "cancelled", "must not be overwritten to 'failed'"


async def test_compliance_gate_node_opens_no_gate_for_a_cancelled_run(monkeypatch):
    interrupt_called = False

    def _fail_if_called(payload):
        nonlocal interrupt_called
        interrupt_called = True
        return {"decision": "approve", "notes": None}

    monkeypatch.setattr(compliance, "interrupt", _fail_if_called)
    run = await _make_run()
    await _cancel(run)

    result = await compliance.compliance_gate_node(_base_state(run))

    assert result == {"status": "rejected"}
    assert interrupt_called is False, "a cancelled run must not open interrupt() at all"
    async with UnitOfWork() as uow:
        gates = await uow.gates.list_for_run(run.id)
        updated_run = await uow.workflow.get_run(run.id)
    assert gates == []
    assert updated_run is not None
    assert updated_run.status == "cancelled", "must not be overwritten to 'failed'"


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
