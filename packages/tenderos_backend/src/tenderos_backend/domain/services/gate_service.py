import uuid
from collections.abc import Awaitable, Callable

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


class TenderStateMovedError(Exception):
    """Plan item 93: the decision was conditional on the bid's state version
    (the final gate's approval, at the version its readiness was just
    recomputed at), and the version moved before the decision could be
    recorded. Nothing was written; the route answers READINESS_CHANGED."""


def _replay_or_conflict(gate: ApprovalGate, decision: str) -> tuple[ApprovalGate, bool]:
    """The answer for a decision on a gate that is no longer pending: the
    same decision as the one recorded is a replay (is_new_decision=False, no
    new outbox row); any other is GateAlreadyDecidedError, which the route
    answers with 409. A gate closed by a cancel records decision None, so
    every decision on it conflicts."""
    if (gate.decision_payload or {}).get("decision") == decision:
        return gate, False
    raise GateAlreadyDecidedError()


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
    require_tender_state_version: int | None = None,
    before_commit: Callable[[UnitOfWork, ApprovalGate], Awaitable[None]] | None = None,
) -> tuple[ApprovalGate, bool]:
    """Record a gate decision. Returns (gate, is_new_decision).

    R5: "a decision is idempotent." If the gate is already decided with this exact
    decision, replay it (is_new_decision=False) rather than erroring or writing
    twice - repeating the same POST (e.g. a retried request using a fresh
    Idempotency-Key by mistake) must not double-enqueue a resume job. A *different*
    decision against an already-decided gate is a real conflict, not a replay.
    Two decisions racing on one pending gate get the same answers (plan item 57):
    the gate update is conditional on status = 'pending', so the one that loses
    is answered like a decision arriving after the other, and the outbox's unique
    idempotency_key stays a backstop rather than the guard.

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

    require_tender_state_version (plan item 93): the decision is recorded
    only if the gate's tender is still at that version, in the same
    conditional UPDATE; otherwise TenderStateMovedError, nothing written.

    PR 16 (plan item 21's limits 1 and 2 for this route): one transaction.
    The gate is read with its row locked, so a second decision or a cancel
    on it waits until this one commits or rolls back. before_commit runs
    last inside the transaction, with the decided (or replayed) gate: the
    route completes its idempotency record there, so the decision and its
    replayable answer commit together, and a crash on either side of the
    commit leaves both or neither. A conflict raises before it runs, so
    nothing is stored and the route releases the key.

    Lock order with a cancel (PR 16 review): a cancel updates its run's row,
    then closes the run's pending gates (cancel_workflow_run). This
    transaction locks the gate first and touches the run's row only through
    its audit row's foreign key check, which takes FOR KEY SHARE, a mode a
    plain run update does not block. So the two orders differ but cannot
    deadlock: the cancel waits for this decision, or this decision waits for
    the cancel. Taking a stronger lock on the run row here (FOR SHARE or
    FOR UPDATE, after the gate's) would deadlock with a cancel; so would the
    cancel locking its run row FOR UPDATE. test_gate_decision_race.py runs
    both orders through the routes.
    """
    async with UnitOfWork() as uow:
        gate = await uow.gates.get_for_update(gate_id)
        if gate is None:
            raise ValueError(f"ApprovalGate {gate_id} not found")

        if gate.status != "pending":
            replayed, is_new = _replay_or_conflict(gate, decision)
            if before_commit is not None:
                await before_commit(uow, replayed)
            await uow.commit()
            return replayed, is_new

        decision_payload: dict = {"decision": decision, "notes": notes, "waived": waived}
        if waived:
            decision_payload["waived_blockers"] = waived_blockers or []

        updated = await uow.gates.record_decision(
            gate_id=gate_id,
            decided_by=decided_by,
            status=status_for_decision(decision),
            decision_payload=decision_payload,
            require_tender_state_version=require_tender_state_version,
        )
        if updated is None:
            # Plan item 57: another request decided (or a cancel closed) the
            # gate between the read above and this update, and the update's
            # status = 'pending' condition matched nothing; nothing has been
            # written. Answer as for a decision arriving after that one did:
            # the winner's decision, re-read now that it has committed.
            await uow.session.refresh(gate)
            if gate.status == "pending":
                # Plan item 93: still pending, so the update's other
                # condition failed: the bid's state version moved.
                raise TenderStateMovedError()
            # Unreachable while the row lock above is held (PR 16): nothing
            # else can decide or close this gate meanwhile. Kept as the
            # answer the conditional update has always given.
            replayed, is_new = _replay_or_conflict(gate, decision)
            if before_commit is not None:
                await before_commit(uow, replayed)
            await uow.commit()
            return replayed, is_new
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
        if before_commit is not None:
            await before_commit(uow, updated)
        await uow.commit()
        return updated, True
