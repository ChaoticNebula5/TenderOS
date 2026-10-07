from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from tenderos_backend.db.models.user import User
from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.domain.services import (
    gate_service,
    gate_target_service,
    readiness_service,
    tender_service,
)

from tenderos_api.dependencies import require_gate_decision_permission, require_permission
from tenderos_api.errors import (
    GateAlreadyDecidedError,
    ReadinessNotWaivedError,
    TargetOwnershipDeniedError,
)
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.gates import (
    CommercialApprovalGateResponse,
    ComplianceReviewGateResponse,
    CostBudgetGateResponse,
    FinalReadinessGateResponse,
    FindingResolutionGateResponse,
    GateDecisionRequest,
    GateResponse,
    RequirementReviewGateResponse,
    ResourceSelectionGateResponse,
)

router = APIRouter(tags=["gates"])

# gate_type -> its matching response subclass, same dict-dispatch style
# orchestration/gates.py's own GATE_TYPE_ACTIONS already uses. Every gate
# row's gate_type is one of these six (Part C's CHECK constraint on
# approval_gates.gate_type), so a KeyError here would mean the DB has a
# gate_type this API layer has never heard of - not a case to catch, a
# schema drift to find out about loudly.
_GATE_TYPE_TO_RESPONSE_CLASS: dict[str, type[BaseModel]] = {
    "requirement_review": RequirementReviewGateResponse,
    "compliance_review": ComplianceReviewGateResponse,
    "resource_selection": ResourceSelectionGateResponse,
    "commercial_approval": CommercialApprovalGateResponse,
    "finding_resolution": FindingResolutionGateResponse,
    "final_readiness": FinalReadinessGateResponse,
    "cost_budget": CostBudgetGateResponse,
}


def _gate_to_response(gate: ApprovalGate) -> GateResponse:
    response_class = _GATE_TYPE_TO_RESPONSE_CLASS[gate.gate_type]
    # cast, not a plain return: the dict lookup above guarantees
    # response_class matches gate.gate_type at runtime (Part C's CHECK
    # constraint backs that), but mypy has no way to narrow a dict's value
    # type down to one specific member of GateResponse's Union from a
    # runtime string key - this tells it what's already true, not what to
    # trust blindly.
    return cast(
        GateResponse,
        response_class(
            gate_id=gate.id,
            gate_type=gate.gate_type,
            workflow_run_id=gate.workflow_run_id,
            status=gate.status,
            targets=gate.request_payload.get("targets", []),
            summary=gate.request_payload.get("summary", {}),
            allowed_decisions=gate.request_payload.get("allowed_decisions", []),
            decision_payload=gate.decision_payload,
            decided_by=gate.decided_by,
            requested_at=gate.requested_at,
            decided_at=gate.decided_at,
        ),
    )


@router.get("/workflow-runs/{run_id}/gates", response_model=list[GateResponse])
async def list_gates(
    run_id: UUID, current_user: User = Depends(require_permission("view_gates"))
) -> list[GateResponse]:
    gates = await gate_service.list_gates_for_run(run_id)
    return [_gate_to_response(g) for g in gates]


@router.get("/gates/{gate_id}", response_model=GateResponse)
async def get_gate(
    gate_id: UUID, current_user: User = Depends(require_permission("view_gates"))
) -> GateResponse:
    gate = await gate_service.get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
    return _gate_to_response(gate)


@router.post("/gates/{gate_id}/decision", response_model=GateResponse)
async def decide_gate(
    gate_id: UUID,
    body: GateDecisionRequest,
    current_user: User = Depends(require_gate_decision_permission),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("gate_decision", required=True)
    ),
) -> GateResponse:
    if idempotency.replay_response is not None:
        # GateResponse is now a discriminated Union alias, not a class - it
        # has no .model_validate of its own. Same dispatch-by-gate_type
        # dict _gate_to_response uses, keyed off the cached body's own
        # gate_type field instead of a live ApprovalGate row.
        _, cached_body = idempotency.replay_response
        response_class = _GATE_TYPE_TO_RESPONSE_CLASS[cached_body["gate_type"]]
        return cast(GateResponse, response_class.model_validate(cached_body))

    gate = await gate_service.get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
    tender = await tender_service.get_tender(gate.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    # B12/Q3: the ONLY enforcement point for "refuses approval until
    # resolved or explicitly waived by authorized actor" (Build Guide
    # stop-gate, verbatim). Must happen here, synchronously, before
    # record_decision - final_gate_node's own interrupt() only receives
    # decision_payload asynchronously, after resume_workflow's RQ job
    # picks up the GateDecisionRecorded event, by which point the decision
    # is already permanently written to approval_gates. A missing report
    # (no readiness_generation run has ever completed for this tender)
    # reads as not-ready - fail closed, never "nothing to waive."
    #
    # CodeRabbit review: this now runs whenever the gate/decision match,
    # not just when body.waive is unset - waived/waived_blockers below
    # need to know whether a bypass was actually NEEDED, which requires
    # checking readiness regardless of what the caller sent. waived is set
    # True only when there really were outstanding blockers this decision
    # bypassed - body.waive=true on an already-ready gate asked for
    # nothing and bypassed nothing, and must not be recorded as a waiver.
    waived = False
    waived_blockers: list[dict] | None = None
    if gate.gate_type == "final_readiness" and body.decision == "approve":
        readiness = await readiness_service.get_latest_readiness(gate.tender_id)
        # Same staleness shape graph_service.check_freshness already
        # guards against: a human can sit on this gate for a while, and
        # something else may bump tender.state_version after readiness_
        # generation_node last ran. Comparing versions here means a STALE
        # ready=True can never silently bypass the waiver requirement -
        # only a report generated against the tender's CURRENT state does.
        is_current = (
            readiness is not None and readiness.tender_state_version == tender.state_version
        )
        # `readiness is None or` first: the same truth value as before (is_current
        # is already False when readiness is None), but stated where mypy can
        # see it, so readiness is narrowed for the .ready read.
        not_ready = readiness is None or not is_current or not readiness.ready
        if not_ready and not body.waive:
            raise ReadinessNotWaivedError()
        if not_ready and body.waive:
            waived = True
            waived_blockers = readiness.blockers if readiness is not None else []

    # Q1: applied unconditionally, before the gate's own decision is
    # recorded, regardless of what that decision turns out to be - a
    # reviewer approving individual targets while still requesting changes
    # on the gate overall is legitimate (see gate_target_service.py's own
    # module comment). gate.tender_id, not anything from the request body,
    # is the trusted anchor apply_target_updates checks every target
    # against.
    try:
        await gate_target_service.apply_target_updates(
            gate.tender_id,
            [u.model_dump() for u in body.target_updates],
            current_user.id,
        )
    except gate_target_service.TargetOwnershipError as exc:
        raise TargetOwnershipDeniedError() from exc

    try:
        updated, _is_new = await gate_service.record_decision(
            gate_id=gate_id,
            organization_id=tender.organization_id,
            decided_by=current_user.id,
            decision=body.decision,
            notes=body.notes,
            waived=waived,
            waived_blockers=waived_blockers,
        )
    except gate_service.GateAlreadyDecidedError as exc:
        raise GateAlreadyDecidedError() from exc

    response = _gate_to_response(updated)
    await idempotency.finalize(200, response.model_dump(mode="json"))
    return response
