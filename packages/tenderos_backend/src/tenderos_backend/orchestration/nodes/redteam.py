import logging
import uuid

from langgraph.types import interrupt

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import gate_summary_service
from tenderos_backend.domain.services.redteam_service import (
    map_finding_to_corrective_task,
    run_red_team,
)
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape every other
    # LLM-backed node in this codebase already establishes. B17: delegates
    # to agents/llm/factory.py::build_llm_client, the real gemini-vs-fake
    # decision.
    return build_llm_client()


async def red_team_node(state: TenderState) -> dict:
    """B11's red_team node - plain sequential, no Send(), no interrupt().
    Sits right after project_graph in both the first pass and every
    revision pass. Runs the full 5-step check list
    (redteam_service.run_red_team) and stashes the tender's CURRENT open
    critical-finding set into state - route_after_red_team and
    targeted_replan_node both only ever read state, never query the DB
    themselves, same "routing functions read state, nodes do the I/O"
    split every existing route_after_* function already follows.

    B12: wrapped in try/except, matching proposal_drafting_node's own
    pattern exactly - an open question left from B11 (this node originally
    had no failure handling at all, unlike proposal_drafting_node/
    project_graph_node, so a real Gemini failure here used to kill the
    whole run instead of degrading it). On failure, open_critical_finding_
    ids is deliberately left OUT of the returned dict rather than set to
    [] - state's own value (whatever the last successful pass computed, or
    the TypedDict default if none ever ran) stays untouched, so a failed
    pass can never look like "nothing open" to route_after_red_team or
    readiness_generation_node. degraded_stages instead makes the failure
    visible to both (readiness_generation_node's own red_team_review_
    incomplete blocker reads exactly this list - see readiness_service.py).
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    tender_id = uuid.UUID(state["tender_id"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    await sync_run_status(workflow_run_id, "red_teaming")

    try:
        client = _build_llm_client()
        open_critical_finding_ids = await run_red_team(
            client, settings.reasoning_model, tender_id, workflow_run_id=workflow_run_id
        )
    except Exception as exc:
        logger.exception("red team review failed for tender %s", tender_id)
        return {"degraded_stages": [f"red_team:{type(exc).__name__}"]}

    return {"open_critical_finding_ids": open_critical_finding_ids}


async def targeted_replan_node(state: TenderState) -> dict:
    """Owns auto-correctability (the plan's Q5 answer): for each open
    critical finding, maps its category to a corrective task_type via
    redteam_service's own TODO(human) function, then dispatches only what's
    actually auto-correctable. Two real outcomes:

    - At least one finding maps to "compliance_analysis": create (or reuse,
      via get_task_by_key's existing dedup-by-key convention) a
      revision-scoped WorkflowTask, set dispatch_task_ids to it,
      revision_dispatch_target="specialist_dispatch".
    - No compliance_analysis task is needed but at least one finding maps
      to "proposal_revision": nothing to create (proposal_drafting isn't
      Send()-dispatched or task-tracked at all - B9's own design),
      revision_dispatch_target="proposal_drafting".

    If NEITHER outcome applies (every open finding maps to None), this is
    B2.4's "no concrete changed input" stop condition: revision_count is
    NOT incremented, pre_revision_finding_ids is NOT touched, and
    revision_dispatch_target="none" sends the run to finding_gate instead.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])

    corrective_task_types: set[str] = set()
    async with UnitOfWork() as uow:
        for finding_id in state["open_critical_finding_ids"]:
            finding = await uow.redteam.get(uuid.UUID(finding_id))
            if finding is None:
                continue
            task_type = map_finding_to_corrective_task(finding.finding_category)
            if task_type is not None:
                corrective_task_types.add(task_type)

    if not corrective_task_types:
        return {"revision_dispatch_target": "none"}

    next_revision = state["revision_count"] + 1
    dispatch_task_ids: list[str] = []
    if "compliance_analysis" in corrective_task_types:
        task_key = f"compliance_analysis:{workflow_run_id}:revision-{next_revision}"
        async with UnitOfWork() as uow:
            existing = await uow.workflow.get_task_by_key(workflow_run_id, task_key)
            if existing is not None:
                task = existing
            else:
                task = await uow.workflow.create_task(
                    workflow_run_id=workflow_run_id,
                    task_key=task_key,
                    task_type="compliance_analysis",
                    assigned_agent="evidence_compliance",
                    priority=10,
                    input_refs={"reason": "redteam_revision"},
                )
            await uow.commit()
        dispatch_task_ids.append(str(task.id))
        revision_dispatch_target = "specialist_dispatch"
    elif "proposal_revision" in corrective_task_types:
        revision_dispatch_target = "proposal_drafting"
    else:
        # A mapped task_type outside the two this block wires up (defensive
        # only - map_finding_to_corrective_task's own contract limits it to
        # these two or None).
        return {"revision_dispatch_target": "none"}

    return {
        "dispatch_task_ids": dispatch_task_ids,
        "pre_revision_finding_ids": state["open_critical_finding_ids"],
        "revision_count": next_revision,
        "revision_dispatch_target": revision_dispatch_target,
    }


async def finding_gate_node(state: TenderState) -> dict:
    """R5's finding_resolution human gate - same shape as
    compliance_gate_node/final_gate_node: interrupt(), create-once via
    deterministic gate_key, route only on "reject". Reachable only after
    review_router has already decided the automatic loop is over (budget
    exhausted, no progress, or nothing auto-correctable) - it has ZERO
    outgoing edges back into the loop (only to final_gate on anything other
    than "reject"), which is what makes the revision loop provably bounded
    independent of the budget check itself: no decision value sent here can
    ever re-enter specialist_dispatch/red_team.
    """
    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key("finding_resolution", state["plan_version"])

    if await is_cancelled(workflow_run_id):
        return {"status": "rejected"}

    tender_id = uuid.UUID(state["tender_id"])

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            findings = await uow.redteam.list_open_for_tender(
                tender_id, severities=gate_summary_service.FINDING_GATE_SEVERITIES
            )
            gate_id = uuid.uuid4()
            payload = build_gate_payload(
                gate_id,
                "finding_resolution",
                workflow_run_id,
                targets=[
                    {"target_type": "redteam_finding", "target_id": str(f.id)} for f in findings
                ],
                # Plan item 73: shared with the gates API's live counts.
                summary=await gate_summary_service.finding_resolution_summary(uow, tender_id),
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender_id,
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="finding_resolution",
                request_payload=payload,
            )
        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_finding_resolution")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # Same posture as reject - do not advance to final_gate.
        # route_after_finding_gate routes "changes_requested" to END, same
        # as "rejected". Does not yet re-open this gate for a corrective
        # resubmission - see final_gate_node's identical note.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    return {}
