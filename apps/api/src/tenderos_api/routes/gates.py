import logging
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from tenderos_backend.core.log_context import bind_log_context
from tenderos_backend.db.models.user import User
from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    gate_service,
    gate_summary_service,
    readiness_service,
    tender_service,
)

from tenderos_api.dependencies import require_gate_decision_permission, require_permission
from tenderos_api.errors import (
    GateAlreadyDecidedError,
    GateTargetUpdatesError,
    ReadinessChangedError,
    ReadinessNotWaivedError,
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
logger = logging.getLogger(__name__)

# Which W2 check refused the final gate's approval (READINESS_CHANGED).
REPORT_CHANGED = "report_changed"
VERSION_MOVED = "version_moved"


def _report_facts(result: readiness_service.ReadinessResult | None) -> dict[str, Any] | None:
    """One readiness report as the refusal line gives it: ids, flags,
    numbers, a time and blocker codes. Never a blocker's message, which
    names documents, requirements or claims."""
    if result is None:
        return None
    return {
        "report_id": str(result.report_id),
        "ready": result.ready,
        "version": result.tender_state_version,
        "last_generated_at": (
            result.last_generated_at.isoformat() if result.last_generated_at else None
        ),
        "blocker_codes": sorted(str(b.get("category")) for b in result.blockers),
        "counts": dict(result.counts),
    }


def _log_readiness_changed(
    gate: ApprovalGate,
    check: str,
    latest: readiness_service.ReadinessResult | None,
    recomputed: readiness_service.ReadinessResult | None,
) -> None:
    """Observability PR 31 (plan item 117, and for production): one INFO line
    each time W2 refuses with READINESS_CHANGED, naming which check fired
    (REPORT_CHANGED: the recompute's report is not the latest stored one
    read just before it; VERSION_MOVED: the bid's version moved before the
    decision was written) and both reports it compared, so the refusal
    explains itself without a probe. `latest`, not "shown": the server never
    knows which report the page showed, only the latest stored one, and
    whether that is the right one to compare against is one of item 117's
    open questions. The gate's tender is bound into the log context first,
    so this line and the rest of the request's carry it."""
    bind_log_context(tender_id=str(gate.tender_id))
    logger.info(
        "final gate %s approval refused with READINESS_CHANGED (%s)",
        gate.id,
        check,
        extra={
            "gate_id": str(gate.id),
            "readiness_check": check,
            "latest_report": _report_facts(latest),
            "recomputed_report": _report_facts(recomputed),
        },
    )


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


def _gate_to_response(gate: ApprovalGate, live_summary: dict | None = None) -> GateResponse:
    response_class = _GATE_TYPE_TO_RESPONSE_CLASS[gate.gate_type]
    extra = {} if live_summary is None else {"live_summary": live_summary}
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
            **extra,
        ),
    )


async def _gate_with_live_counts(gate: ApprovalGate) -> GateResponse:
    """Plan item 73: a pending gate's response carries its counts as they
    are now (gate_summary_service.live_summary), so the panel can show them
    above the snapshot taken when the gate opened."""
    return _gate_to_response(gate, await gate_summary_service.live_summary(gate))


@router.get("/workflow-runs/{run_id}/gates", response_model=list[GateResponse])
async def list_gates(
    run_id: UUID, current_user: User = Depends(require_permission("view_gates"))
) -> list[GateResponse]:
    gates = await gate_service.list_gates_for_run(run_id)
    return [await _gate_with_live_counts(g) for g in gates]


@router.get("/gates/{gate_id}", response_model=GateResponse)
async def get_gate(
    gate_id: UUID, current_user: User = Depends(require_permission("view_gates"))
) -> GateResponse:
    gate = await gate_service.get_gate(gate_id)
    if gate is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gate not found")
    return await _gate_with_live_counts(gate)


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
    # PR 16 (plan item 21's note with item 67's gate-target note, user
    # decision 2026-11-17): a decision decides the gate only, on every gate,
    # decided or not. Each item is approved, selected or resolved on its own
    # route (the specs give each its own, and every screen uses them). Before
    # this, target_updates were applied in their own committed transaction
    # ahead of the decision and checked only against the gate's tender, so a
    # refused decision left them applied and one gate's decision could change
    # another gate's targets (both observed on the fake stack). No screen and
    # not the frozen E2E spec ever sent them. Refused before anything is read
    # or written; an empty list, the spec's example payload, is accepted.
    if body.target_updates:
        raise GateTargetUpdatesError()
    tender = await tender_service.get_tender(gate.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    # B12/Q3: the ONLY enforcement point for the Build Guide's stop-gate rule
    # (paraphrased): approval is refused until every blocker is resolved or an
    # authorized actor explicitly waives it. Must happen here, synchronously, before
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
    #
    # Plan item 93 (W2, user decision): the approval recomputes readiness
    # instead of trusting the latest stored report, because much of what
    # readiness reads (red-team findings, pricing findings, the graph's
    # projection, Neo4j's reachability) changes without moving the bid's
    # state version. The report current before this request (the one the
    # Readiness page shows) is read first; the generate then stores or
    # reuses one report per content and makes it the newest. A different id
    # means readiness changed since that report, so the approval is refused
    # with READINESS_CHANGED; the recomputed report is now the current one,
    # so a second attempt passes. Plan items 94 and 96: the recompute reads
    # the same facts as the Readiness page's generate (the recorded red-team
    # passes, not the gate payload's frozen degraded stages), so the two
    # agree, and a later successful red-team pass is seen here too. The
    # decision is then recorded only if the bid's version is still the
    # recomputed report's (a conditional write).
    waived = False
    waived_blockers: list[dict] | None = None
    required_version: int | None = None
    latest: readiness_service.ReadinessResult | None = None
    fresh: readiness_service.ReadinessResult | None = None
    if gate.gate_type == "final_readiness" and body.decision == "approve":
        latest = await readiness_service.get_latest_readiness(gate.tender_id)
        fresh = await readiness_service.evaluate_readiness(gate.tender_id)
        if latest is None or fresh.report_id != latest.report_id:
            _log_readiness_changed(gate, REPORT_CHANGED, latest, fresh)
            raise ReadinessChangedError()
        required_version = fresh.tender_state_version
        if not fresh.ready and not body.waive:
            raise ReadinessNotWaivedError()
        if not fresh.ready and body.waive:
            waived = True
            waived_blockers = fresh.blockers

    # Plan item 21, limits 1 and 2, for this route (PR 16): the idempotency
    # record completes in the decision's own transaction, so the decision and
    # its replayable answer commit together. A retry after a crash on either
    # side of that commit replays or decides again, never both; a refused
    # decision stores nothing and the dependency releases the key.
    async def store_response(uow: UnitOfWork, decided: ApprovalGate) -> None:
        stored = _gate_to_response(decided)
        await idempotency.finalize_in(uow, 200, stored.model_dump(mode="json"))

    try:
        updated, _is_new = await gate_service.record_decision(
            gate_id=gate_id,
            organization_id=tender.organization_id,
            decided_by=current_user.id,
            decision=body.decision,
            notes=body.notes,
            waived=waived,
            waived_blockers=waived_blockers,
            require_tender_state_version=required_version,
            before_commit=store_response,
        )
    except gate_service.GateAlreadyDecidedError as exc:
        # Q7: who decided and when, for the panel to say so.
        raise GateAlreadyDecidedError(
            decided_by=str(exc.decided_by) if exc.decided_by is not None else None,
            decided_by_name=exc.decided_by_name,
            decided_at=exc.decided_at.isoformat() if exc.decided_at is not None else None,
            decision=exc.decision,
        ) from exc
    except gate_service.TenderStateMovedError as exc:
        _log_readiness_changed(gate, VERSION_MOVED, latest, fresh)
        raise ReadinessChangedError() from exc

    return _gate_to_response(updated)
