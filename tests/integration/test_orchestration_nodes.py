import pytest
from _helpers import make_ready_run_inputs
from langgraph.graph import END
from langgraph.types import Send
from sqlalchemy import select
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import workflow_service
from tenderos_backend.orchestration import routing
from tenderos_backend.orchestration.nodes import (
    commercial,
    compliance,
    final_gate,
    redteam,
    requirement,
    talent,
)
from tenderos_backend.orchestration.nodes.specialist_dispatch import (
    _TASK_TYPE_TO_NODE,
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
        "degraded_stages": [],
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


@pytest.mark.parametrize(
    ("module", "node", "gate_type"),
    [
        (requirement, requirement.requirement_gate_node, "requirement_review"),
        (compliance, compliance.compliance_gate_node, "compliance_review"),
        (talent, talent.resource_selection_gate_node, "resource_selection"),
        (commercial, commercial.commercial_approval_gate_node, "commercial_approval"),
        (redteam, redteam.finding_gate_node, "finding_resolution"),
        (final_gate, final_gate.final_gate_node, "final_readiness"),
    ],
)
async def test_gate_node_request_changes_does_not_advance(module, node, gate_type, monkeypatch):
    """Regression test for the audit's worst finding: every gate node
    checked only decision == "reject" - request_changes (a real,
    ALLOWED_DECISIONS-valid decision) fell through to the same branch
    approve takes, so a human explicitly asking for changes silently
    advanced the run (at final_gate, all the way to "ready"). Parameterized
    across all six gate types, not just final_gate, since the same bug was
    structurally identical in each.
    """
    monkeypatch.setattr(
        module, "interrupt", lambda payload: {"decision": "request_changes", "notes": "fix this"}
    )
    run = await _make_run()

    result = await node(_base_state(run))

    assert result == {"status": "changes_requested"}, (
        f"{gate_type} gate advanced on request_changes instead of halting: {result}"
    )
    async with UnitOfWork() as uow:
        updated_run = await uow.workflow.get_run(run.id)
    assert updated_run is not None
    assert updated_run.status == "waiting_for_human", (
        "request_changes must read as waiting_for_human, not silently "
        f"'completed'/'running': got {updated_run.status!r}"
    )


@pytest.mark.parametrize(
    ("route_fn", "advance_target"),
    [
        (routing.route_after_requirement_gate, "specialist_dispatch"),
        (routing.route_after_compliance_gate, "resource_selection_gate"),
        (routing.route_after_resource_selection_gate, "commercial_approval_gate"),
        (routing.route_after_commercial_approval_gate, "proposal_drafting"),
        (routing.route_after_finding_gate, "final_gate"),
    ],
)
async def test_route_after_gate_halts_the_graph_on_request_changes(route_fn, advance_target):
    """The node-level test above proves the node itself doesn't silently
    approve; this proves the graph itself doesn't either - a route_after_*
    function that still only checked "rejected" would send
    "changes_requested" down the same advance_target edge every approve
    takes. final_gate has no route_after function (add_edge("final_gate",
    END) is unconditional) so it isn't part of this table.
    """
    halted = route_fn({"status": "changes_requested"})
    advanced = route_fn({"status": "approved"})

    assert halted == END
    assert advanced == advance_target


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


# B11: dispatch_specialists now looks up each dispatched task's real
# task_type from Postgres (needed so a revision can Send() to just one
# specialist instead of always all 3 - see specialist_dispatch.py's own
# comment) - fake string ids like "task-a" no longer work, since they're
# parsed as real UUIDs and looked up against real WorkflowTask rows.
async def _make_dispatch_tasks(run: WorkflowRun) -> dict[str, str]:
    async with UnitOfWork() as uow:
        task_ids: dict[str, str] = {}
        for task_type, node_name in _TASK_TYPE_TO_NODE.items():
            task = await uow.workflow.create_task(
                workflow_run_id=run.id,
                task_key=f"{task_type}:{run.id}",
                task_type=task_type,
                assigned_agent=node_name,
                priority=5,
                input_refs={},
            )
            task_ids[node_name] = str(task.id)
        await uow.commit()
        return task_ids


async def test_dispatch_specialists_sends_one_per_specialist_node():
    run = await _make_run()
    node_task_ids = await _make_dispatch_tasks(run)
    state = {**_base_state(run), "dispatch_task_ids": list(node_task_ids.values())}

    sends = await dispatch_specialists(state)

    assert len(sends) == 3
    assert all(isinstance(s, Send) for s in sends)
    assert {s.node: s.arg["task_id"] for s in sends} == node_task_ids


async def test_dispatch_specialists_sends_only_the_dispatched_task_types():
    # The actual B11 behavior change: a revision that only needs
    # compliance_analysis re-run must Send() to ONLY that node, not all 3 -
    # this is what makes a revision "targeted."
    run = await _make_run()
    node_task_ids = await _make_dispatch_tasks(run)
    state = {**_base_state(run), "dispatch_task_ids": [node_task_ids["compliance_analysis"]]}

    sends = await dispatch_specialists(state)

    assert len(sends) == 1
    assert sends[0].node == "compliance_analysis"
    assert sends[0].arg["task_id"] == node_task_ids["compliance_analysis"]


async def test_dispatch_specialists_preserves_shared_state_in_each_payload():
    run = await _make_run()
    node_task_ids = await _make_dispatch_tasks(run)
    state = {
        **_base_state(run),
        "status": "running_specialists",
        "dispatch_task_ids": list(node_task_ids.values()),
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
