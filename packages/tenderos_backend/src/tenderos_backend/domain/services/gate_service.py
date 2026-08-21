import uuid

from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    EVENT_GATE_DECISION_RECORDED,
    gate_decision_recorded_idempotency_key,
    gate_decision_recorded_payload,
)
from tenderos_backend.orchestration.gates import status_for_decision


class GateAlreadyDecidedError(Exception):
    pass


async def get_gate(gate_id: uuid.UUID) -> ApprovalGate | None:
    async with UnitOfWork() as uow:
        return await uow.gates.get(gate_id)


async def list_gates_for_run(workflow_run_id: uuid.UUID) -> list[ApprovalGate]:
    async with UnitOfWork() as uow:
        return await uow.gates.list_for_run(workflow_run_id)


async def record_decision(
    gate_id: uuid.UUID,
    organization_id: uuid.UUID,
    decided_by: uuid.UUID,
    decision: str,
    notes: str | None,
    waived: bool = False,
    waived_blockers: list | None = None,
) -> tuple[ApprovalGate, bool]:
    """Record a gate decision. Returns (gate, is_new_decision).

    R5: "a decision is idempotent." If the gate is already decided with this exact
    decision, replay it (is_new_decision=False) rather than erroring or writing
    twice - repeating the same POST (e.g. a retried request using a fresh
    Idempotency-Key by mistake) must not double-enqueue a resume job. A *different*
    decision against an already-decided gate is a real conflict, not a replay.

    Assumes the caller already confirmed the gate exists (the route resolves
    gate_type -> RBAC action from it before this is ever called, so a second
    "not found" branch here would be unreachable).

    waived/waived_blockers (CodeRabbit review): decide_gate (routes/gates.py)
    passes waived=True only when this specific decision actually bypassed an
    outstanding READINESS_NOT_WAIVED rejection - not merely whenever the
    caller's request body happened to set waive=true (e.g. on an already-
    ready gate, where waive=true asked for nothing and bypassed nothing).
    Persisted in both the gate's own decision_payload and the audit event,
    together with which blockers were outstanding at the moment of waiving -
    a waiver enforced but not recorded is an auditable bypass with no
    audit trail, which defeats the entire reason this was built as a
    distinct act instead of letting a bare "approve" through.
    """
    async with UnitOfWork() as uow:
        gate = await uow.gates.get(gate_id)
        if gate is None:
            raise ValueError(f"ApprovalGate {gate_id} not found")

        if gate.status != "pending":
            already_recorded = (gate.decision_payload or {}).get("decision")
            if already_recorded == decision:
                return gate, False
            raise GateAlreadyDecidedError()

        decision_payload: dict = {"decision": decision, "notes": notes, "waived": waived}
        if waived:
            decision_payload["waived_blockers"] = waived_blockers or []

        updated = await uow.gates.record_decision(
            gate_id=gate_id,
            decided_by=decided_by,
            status=status_for_decision(decision),
            decision_payload=decision_payload,
        )
        await uow.outbox.create(
            aggregate_type=AGGREGATE_APPROVAL_GATE,
            aggregate_id=gate_id,
            event_type=EVENT_GATE_DECISION_RECORDED,
            payload=gate_decision_recorded_payload(gate_id, gate.workflow_run_id),
            idempotency_key=gate_decision_recorded_idempotency_key(gate_id),
        )
        audit_after: dict = {"decision": decision, "waived": waived}
        if waived:
            audit_after["waived_blockers"] = waived_blockers or []
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(decided_by),
            action="record_gate_decision",
            tender_id=gate.tender_id,
            workflow_run_id=gate.workflow_run_id,
            target_type="approval_gate",
            target_id=gate_id,
            after=audit_after,
        )
        await uow.commit()
        return updated, True
