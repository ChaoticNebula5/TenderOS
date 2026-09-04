import uuid

from langgraph.types import interrupt

from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload
from tenderos_backend.orchestration.state import TenderState


async def cost_budget_gate_node(state: TenderState) -> dict:
    """I5's cost-budget gate (Build Guide :2755-2763) - a spec deviation now
    closed, not a feature added (see core/config.py's own max_tender_cost_usd
    comment for the full record: MAX_TENDER_COST_USD sat in .env.example for
    16 blocks reading like a real cap while this gate was never built).

    Sits immediately before specialist_dispatch, the single highest-cost
    step in the graph (3 parallel Send()-dispatched branches, each a real
    per-item LLM-call loop) - the same "prevents new tasks" checkpoint I6
    already established for cancellation (specialist_dispatch.py's own
    is_cancelled check). Reached from BOTH route_after_requirement_gate
    (first pass) and route_after_targeted_replan (every revision pass) -
    and, on the revision path, from BOTH of targeted_replan's own two
    live targets (specialist_dispatch AND proposal_drafting), not just the
    specialist-heavy one. B2.4's own "cost budget exceeded" stop condition
    (:731) names the whole proposal<->red-team loop, not half of it - a
    proposal_drafting-only revision pass still spends real money and still
    needs to be caught, even though it's the smaller of the two costs.

    "Projected cost" (I5's own wording) is read here as "accumulated
    actual cost so far, checked before the next expensive phase starts" -
    there is no cost-forecasting model anywhere in this codebase, and
    building one is real, unscoped new work this gate does not attempt.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    async with UnitOfWork() as uow:
        # float, not Decimal, from here on - settings.max_tender_cost_usd
        # is a plain float, and Python allows comparing Decimal to float
        # but not subtracting one from the other (a real TypeError caught
        # by this feature's own integration test, not assumed away).
        accumulated = float(await uow.agent_traces.sum_cost_for_run(workflow_run_id))

    if accumulated < settings.max_tender_cost_usd:
        # The common path: under budget, no gate, no interrupt() at all -
        # this node is a no-op the overwhelming majority of the time.
        return {}

    tender_id = uuid.UUID(state["tender_id"])
    # NOT gates.derive_gate_key(...) unmodified. plan_version is bumped
    # exactly once, by supervisor_node, and never again on a revision pass
    # (checked directly - no other file in orchestration/ ever assigns
    # plan_version + 1), while revision_count genuinely increments per
    # revision (redteam.py's targeted_replan_node). Without folding
    # revision_count in here, a first pass that exceeds budget and gets
    # approved, followed by a LATER revision pass that also exceeds
    # budget, would compute the identical key, find the already-approved
    # gate via get_by_key, never open a new pending one - and still call
    # interrupt() below regardless, pausing the thread with no visible
    # gate anywhere to approve. Permanently stuck, not merely delayed.
    # Don't change derive_gate_key's own one-arg signature for this - the
    # other six gates all depend on its current contract.
    gate_key = f"cost_budget:plan-{state['plan_version']}:rev-{state.get('revision_count', 0)}"

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            gate_id = uuid.uuid4()
            overage = accumulated - settings.max_tender_cost_usd
            payload = build_gate_payload(
                gate_id,
                "cost_budget",
                workflow_run_id,
                # No per-item targets - unlike requirement_review/
                # compliance_review's own lists of requirements/entries,
                # there is no natural set of individual objects this gate
                # decides on; the decision is about the RUN's own spend as
                # a whole. Same empty-list precedent final_readiness's own
                # gate already uses.
                targets=[],
                summary={
                    "accumulated_cost_usd": accumulated,
                    "budget_usd": settings.max_tender_cost_usd,
                    "overage_usd": overage,
                },
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="cost_budget",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_cost_budget_review")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as every other gate - do not advance to more spend.
        # route_after_cost_budget_gate routes "changes_requested" to END,
        # same as "rejected".
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    # Approved: continue spending. Which status write is correct depends
    # on which downstream target this gate is actually sitting in front
    # of. revision_dispatch_target is absent entirely on the first-pass
    # path (route_after_requirement_gate) and "specialist_dispatch" on a
    # specialist-bound revision pass - both need an explicit write here,
    # copying requirement_gate_node's own approve path exactly, because
    # neither specialist_dispatch (a no-op lambda) nor any of the three
    # parallel analysis nodes ever write state["status"] themselves. A
    # proposal_drafting-bound revision pass is different: proposal_
    # drafting_node writes its own "drafting" status unconditionally at
    # its own entry (confirmed directly) - returning {} here, copying
    # commercial_approval_gate_node's own precedent for the identical
    # reason, is correct, not a missing write.
    if state.get("revision_dispatch_target") == "proposal_drafting":
        return {}
    await sync_run_status(workflow_run_id, "running_specialists")
    return {"status": "running_specialists"}
