from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.domain.services import gate_service, tender_service

from tenderos_api.dependencies import require_gate_decision_permission, require_permission
from tenderos_api.errors import GateAlreadyDecidedError
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.gates import GateDecisionRequest, GateResponse

router = APIRouter(tags=["gates"])


def _gate_to_response(gate: ApprovalGate) -> GateResponse:
    return GateResponse(
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
    )


@router.get("/workflow-runs/{run_id}/gates", response_model=list[GateResponse])
async def list_gates(
    run_id: UUID, current_user: User = Depends(require_permission("create_bid"))
) -> list[GateResponse]:
    gates = await gate_service.list_gates_for_run(run_id)
    return [_gate_to_response(g) for g in gates]


@router.get("/gates/{gate_id}", response_model=GateResponse)
async def get_gate(
    gate_id: UUID, current_user: User = Depends(require_permission("create_bid"))
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
        _, cached_body = idempotency.replay_response
        return GateResponse.model_validate(cached_body)

    gate = await gate_service.get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
    tender = await tender_service.get_tender(gate.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    try:
        updated, _is_new = await gate_service.record_decision(
            gate_id=gate_id,
            organization_id=tender.organization_id,
            decided_by=current_user.id,
            decision=body.decision,
            notes=body.notes,
        )
    except gate_service.GateAlreadyDecidedError as exc:
        raise GateAlreadyDecidedError() from exc

    response = _gate_to_response(updated)
    await idempotency.finalize(200, response.model_dump(mode="json"))
    return response
