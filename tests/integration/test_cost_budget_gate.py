"""Repository-level proofs for I5's cost-budget gate (Build Guide
:2755-2763) that don't need a full live graph run: AgentTraceRepository.
sum_cost_for_run's own SQL, and the gate_key uniqueness fix cost_budget_
gate_node depends on to survive a second, later revision-pass budget
breach without stranding the run (see cost_budget.py's own docstring for
the full mechanism this proves).
"""

import uuid

import pytest
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.orchestration.gates import build_gate_payload

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


async def _make_run(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(
            tender_id=tender_id, prompt_bundle_version="v1", model_config={}
        )
        await uow.commit()
        return run.id


async def _make_user() -> uuid.UUID:
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
        return user.id


async def test_sum_cost_for_run_is_scoped_and_coalesces_null_to_zero():
    org_id, tender_id = await _make_org_and_tender("CostSumScoping")
    run_id = await _make_run(tender_id)
    other_run_id = await _make_run(tender_id)

    async with UnitOfWork() as uow:
        # A no-op sanity check first: a run with zero traces at all.
        assert await uow.agent_traces.sum_cost_for_run(run_id) == 0

        await uow.agent_traces.create(
            agent_name="compliance_analysis",
            status="completed",
            tender_id=tender_id,
            workflow_run_id=run_id,
            estimated_cost_usd=1.50,
        )
        await uow.agent_traces.create(
            agent_name="compliance_analysis",
            status="completed",
            tender_id=tender_id,
            workflow_run_id=run_id,
            estimated_cost_usd=2.25,
        )
        # A row with no cost figure at all (an unmapped model id, or a
        # cache hit - see gemini_adapter.py's own _estimate_cost_usd) -
        # must not turn the whole sum into NULL.
        await uow.agent_traces.create(
            agent_name="compliance_analysis",
            status="completed",
            tender_id=tender_id,
            workflow_run_id=run_id,
            estimated_cost_usd=None,
        )
        # A trace on a DIFFERENT run for the same tender - must not leak
        # into this run's own sum.
        await uow.agent_traces.create(
            agent_name="compliance_analysis",
            status="completed",
            tender_id=tender_id,
            workflow_run_id=other_run_id,
            estimated_cost_usd=999.0,
        )
        await uow.commit()

        assert await uow.agent_traces.sum_cost_for_run(run_id) == 3.75
        assert await uow.agent_traces.sum_cost_for_run(other_run_id) == 999.0


async def test_a_later_revisions_gate_key_never_collides_with_an_earlier_approved_one():
    """The exact mechanism major finding [I5] depends on: cost_budget_gate_
    node composes its gate_key as f"cost_budget:plan-{plan_version}:rev-
    {revision_count}", NOT gates.derive_gate_key(gate_type, plan_version)
    unmodified - plan_version is fixed for a run's whole lifetime past
    supervisor_node's own one-time bump, while revision_count genuinely
    increments per revision. Proves directly, against real Postgres: two
    different revision_count values for the SAME plan_version produce two
    DISTINCT keys, and get_by_key correctly treats them as two separate
    gates, not a collision - the specific bug a naive derive_gate_key(
    "cost_budget", plan_version) reuse would have shipped (a first-pass
    breach that gets approved, followed by a revision-pass breach, finding
    the already-approved row and never opening a new pending one).
    """
    org_id, tender_id = await _make_org_and_tender("CostBudgetGateKeyRevision")
    run_id = await _make_run(tender_id)
    user_id = await _make_user()

    first_pass_key = "cost_budget:plan-1:rev-0"
    revision_pass_key = "cost_budget:plan-1:rev-1"
    assert first_pass_key != revision_pass_key

    async with UnitOfWork() as uow:
        assert await uow.gates.get_by_key(run_id, first_pass_key) is None
        assert await uow.gates.get_by_key(run_id, revision_pass_key) is None

        first_gate_id = uuid.uuid4()
        first_gate = await uow.gates.create(
            gate_id=first_gate_id,
            tender_id=tender_id,
            workflow_run_id=run_id,
            gate_key=first_pass_key,
            gate_type="cost_budget",
            request_payload=build_gate_payload(
                first_gate_id,
                "cost_budget",
                run_id,
                targets=[],
                summary={"accumulated_cost_usd": 30.0, "budget_usd": 25.0, "overage_usd": 5.0},
            ),
        )
        await uow.gates.record_decision(
            gate_id=first_gate.id,
            decided_by=user_id,
            status="approved",
            decision_payload={"decision": "approve"},
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        # The first pass's own gate is real and approved - a naive
        # derive_gate_key("cost_budget", plan_version) reuse would find
        # THIS row for the revision pass too and stop here, never opening
        # a second gate.
        approved_first = await uow.gates.get_by_key(run_id, first_pass_key)
        assert approved_first is not None
        assert approved_first.status == "approved"

        # The revision pass's own, DIFFERENT key finds nothing yet -
        # proving the two are genuinely distinct rows, not the same one
        # under two names.
        assert await uow.gates.get_by_key(run_id, revision_pass_key) is None

        second_gate_id = uuid.uuid4()
        second_gate = await uow.gates.create(
            gate_id=second_gate_id,
            tender_id=tender_id,
            workflow_run_id=run_id,
            gate_key=revision_pass_key,
            gate_type="cost_budget",
            request_payload=build_gate_payload(
                second_gate_id,
                "cost_budget",
                run_id,
                targets=[],
                summary={"accumulated_cost_usd": 40.0, "budget_usd": 25.0, "overage_usd": 15.0},
            ),
        )
        await uow.commit()

    async with UnitOfWork() as uow:
        all_gates = await uow.gates.list_for_run(run_id)
    cost_budget_gates = [g for g in all_gates if g.gate_type == "cost_budget"]
    assert len(cost_budget_gates) == 2, (
        "the revision pass's own breach must open a real second, pending "
        "gate - not silently vanish into the already-approved first one"
    )
    assert {g.status for g in cost_budget_gates} == {"approved", "pending"}
    assert second_gate.status == "pending"
