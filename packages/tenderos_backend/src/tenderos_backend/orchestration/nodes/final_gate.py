import uuid

from langgraph.types import interrupt

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.readiness_service import get_report
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.state import TenderState


async def final_gate_node(state: TenderState) -> dict:
    """R5's final_readiness human gate. B12: now sits immediately after
    readiness_generation (see graph.py's own edge), and its payload's
    summary carries that run's real ready/blockers/counts - the "waived by
    authorized actor" enforcement itself lives in routes/gates.py's
    decide_gate, not here (see that file's own comment for why: gate
    decisions are recorded synchronously at the API layer and only
    resumed into this interrupt() asynchronously later, by which point
    the decision is already permanently committed to approval_gates -
    too late for this node to reject anything). This node's own job is
    narrower: surface the truth, don't decide the policy."""

    workflow_run_id = uuid.UUID(state["workflow_run_id"])
    gate_key = derive_gate_key(
        "final_readiness",
        state["plan_version"],
    )

    if await is_cancelled(workflow_run_id):
        # I6: don't open a new gate for a run that's already been cancelled.
        # No sync_run_status call here either - see requirement_gate_node's
        # identical comment.
        return {"status": "rejected"}

    async with UnitOfWork() as uow:
        gate = await uow.gates.get_by_key(workflow_run_id, gate_key)

        if gate is None:
            gate_id = uuid.uuid4()
            # degraded_stages accumulates every essential-stage failure
            # swallowed earlier in this run (proposal_drafting_node,
            # project_graph_node, red_team_node - see TenderState's own
            # comment on the field). Surfaced here, not auto-blocked: a
            # human deciding this gate needs to SEE "proposal drafting
            # failed" rather than approve a silently-thin or stale-graph
            # bid without knowing why - the explicit-degraded-state
            # choice, not a hard block, since this is the one place in the
            # whole run a human is already looking before anything is
            # marked "ready".
            #
            # B12: ready/blockers/counts come from the readiness report
            # readiness_generation_node just persisted immediately before
            # this node ran (state["readiness_report_id"]) - a human
            # deciding this gate sees the SAME transparent blocker list
            # PRD §10.13 requires, not just the coarser degraded_stages
            # signal B3-B11 had alone. A missing report (readiness_
            # report_id somehow None) reads as not-ready, same
            # fail-closed posture the waiver check in routes/gates.py
            # takes on the identical case.
            report = None
            if state.get("readiness_report_id"):
                report = await get_report(uuid.UUID(state["readiness_report_id"]))
            payload = build_gate_payload(
                gate_id,
                "final_readiness",
                workflow_run_id,
                targets=[],
                summary={
                    "ready": report.ready if report else False,
                    "blockers": report.blockers if report else [],
                    "counts": report.counts if report else {},
                    "degraded_stages": state.get("degraded_stages", []),
                },
            )
            gate = await uow.gates.create(
                gate_id=gate_id,
                tender_id=uuid.UUID(state["tender_id"]),
                workflow_run_id=workflow_run_id,
                gate_key=gate_key,
                gate_type="final_readiness",
                request_payload=payload,
            )

        payload = gate.request_payload
        await uow.commit()

    await sync_run_status(workflow_run_id, "awaiting_final_approval")

    decision_payload = interrupt(payload)
    if decision_payload["decision"] == "reject":
        await sync_run_status(workflow_run_id, "rejected")
        return {"status": "rejected"}
    if decision_payload["decision"] == "request_changes":
        # A human explicitly declining to approve must never fall through
        # to the same "ready" branch approve takes - final_gate is the
        # workflow's last node (add_edge("final_gate", END), unconditional),
        # so there is no routing function to also fix here; not advancing
        # to "ready" IS the whole fix. Does not yet re-open this gate for a
        # corrective resubmission - same scope boundary the reject path's
        # own KNOWN_LIMITATIONS entry already carries.
        await sync_run_status(workflow_run_id, "changes_requested")
        return {"status": "changes_requested"}

    await sync_run_status(workflow_run_id, "ready")
    return {"status": "ready"}
