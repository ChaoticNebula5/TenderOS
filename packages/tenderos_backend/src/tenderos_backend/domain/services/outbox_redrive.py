"""Plan item 20 (decision 0034): re-drive dead-lettered outbox events.

An event is dead-lettered (`status = 'failed'`) after its 8th counted
failure, and only an enqueue that raises something other than an
infrastructure error counts (outbox_dispatcher._dispatch_claimed_event), so a
dead letter almost always means its route could not handle the event, not
that Redis was down. Nothing re-delivers it on its own.

A re-drive resets the outbox row and nothing else: `status` to `pending`,
`attempt_count` to 0 and `available_at` to now(), in one conditional UPDATE
(OutboxRepository.reset_failed). The dispatcher then delivers it on its next
pass, through the same route, job id and consumer guard as any delivery. This
module never enqueues to Redis, and never writes a run, an operation, a
reanalysis claim or a document.

It reports the guards instead of fighting them. Before resetting, it reads
what the event's job would find and refuses, with one line saying why, an
event whose job would do nothing:
- a run the dead letter already failed as DISPATCH_FAILED (plan item 19), or
  one that has since finished;
- an operation no longer queued (the dead letter fails it, Phase 4 async
  operations);
- a reanalysis whose claim has completed, or whose lease is still live
  (plan item 18);
- a document already ingested, an import batch already completed, a
  requirement already verified for this event (the Phase 4 consumer guards).
The read and the reset are not atomic. If the target changes in between, the
consumer's guard stops the job, as it does for every redelivery.

Output and logs carry ids, event types, statuses and these lines only, never
an event's payload (it can hold tender data): the payload is read only for
the ids its route reads.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import WorkflowRun
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_ASYNC_OPERATION,
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    AGGREGATE_REQUIREMENT,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_OPERATION_REQUESTED,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    EVENT_WORKFLOW_RUN_CREATED,
)
from tenderos_backend.domain.services.requirement_service import (
    verification_recorded_for_event,
)

logger = logging.getLogger(__name__)

# --list shows at most this many dead letters, oldest first.
LIST_LIMIT = 200

_NO_SUCH_EVENT = "refused: no such outbox event"


@dataclass(frozen=True)
class RedriveContext:
    """What the checks need from the worker, passed in so this package never
    imports it: the ingestion job's parser version (its consumer guard is per
    version) and the dispatcher's attempt limit (for the "pending" line)."""

    parser_version: str
    max_attempts: int


@dataclass(frozen=True)
class RedriveResult:
    """One event's outcome. `line` is the one line printed and logged for it:
    "re-driven: ...", "would re-drive: ..." or "refused: ..."."""

    event_id: uuid.UUID
    event_type: str | None
    status: str | None
    redriven: bool
    refused: bool
    line: str


class _UnreadablePayload(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# A check returns whether the event's job would still do something, and the
# reason: what the target's state is.
_Check = Callable[[UnitOfWork, OutboxEvent, RedriveContext], Awaitable[tuple[bool, str]]]


def _payload_uuid(event: OutboxEvent, key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(event.payload[key]))
    except (KeyError, TypeError, ValueError) as exc:
        raise _UnreadablePayload(key) from exc


def _payload_int(event: OutboxEvent, key: str) -> int:
    value = event.payload.get(key) if isinstance(event.payload, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _UnreadablePayload(key)
    return value


def _at(moment: datetime | None) -> str:
    if moment is None:
        return "an unrecorded time"
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _run_moved_on(run: WorkflowRun, nothing_to: str) -> str:
    if run.status == "failed":
        code = f" ({run.error_code})" if run.error_code else ""
        return f"run {run.id} is failed{code}: start processing again for a new run"
    return f"run {run.id} is {run.status}: nothing for this event to {nothing_to}"


async def _check_run_created(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    run_id = _payload_uuid(event, "workflow_run_id")
    run = await uow.workflow.get_run(run_id)
    if run is None:
        return False, f"run {run_id} no longer exists"
    if run.status != "queued":
        return False, _run_moved_on(run, "start")
    return True, f"run {run_id} is queued"


async def _check_gate_decision(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    run_id = _payload_uuid(event, "workflow_run_id")
    gate_id = _payload_uuid(event, "gate_id")
    run = await uow.workflow.get_run(run_id)
    if run is None:
        return False, f"run {run_id} no longer exists"
    if run.status != "waiting_for_human":
        return False, _run_moved_on(run, "resume")
    gates = await uow.gates.list_for_run(run_id)
    gate = next((g for g in gates if g.id == gate_id), None)
    if gate is None:
        return False, f"gate {gate_id} no longer exists"
    if gates[-1].id != gate_id:
        return False, f"run {run_id} is waiting at a later gate {gates[-1].id}"
    if gate.status == "pending":
        return False, f"gate {gate_id} has no decision recorded"
    return True, f"run {run_id} is waiting at gate {gate_id}, decided {gate.status}"


async def _check_operation(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    operation_id = _payload_uuid(event, "operation_id")
    operation = await uow.operations.get(operation_id)
    if operation is None:
        return False, f"operation {operation_id} no longer exists"
    if operation.status == "queued":
        return True, f"operation {operation_id} is queued"
    if operation.status == "failed":
        code = f" ({operation.error_code})" if operation.error_code else ""
        return False, f"operation {operation_id} is failed{code}: request it again"
    return False, f"operation {operation_id} is {operation.status}: nothing for this event to start"


async def _check_selection_change(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    tender_id = _payload_uuid(event, "tender_id")
    knowledge_item_id = _payload_uuid(event, "knowledge_item_id")
    state_version = _payload_int(event, "state_version")
    found = await uow.reanalysis_claims.get_with_lease(tender_id, knowledge_item_id, state_version)
    if found is None:
        return True, "no reanalysis has started for this change"
    claim, lease_live = found
    if claim.status == "completed":
        completed = _at(claim.completed_at)
        return False, f"the reanalysis for this change already completed at {completed}"
    lease_end = _at(claim.locked_until)
    if lease_live:
        return False, (
            f"a delivery is running now; its lease runs until {lease_end}: "
            "re-drive after that if it has not completed"
        )
    return True, f"the last delivery's lease lapsed at {lease_end}; the job takes its claim over"


async def _check_document(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    document_id = _payload_uuid(event, "document_id")
    if await uow.documents.get(document_id) is None:
        return False, f"document {document_id} no longer exists"
    run = await uow.documents.get_ingestion_run(document_id, ctx.parser_version)
    if run is not None and run.status == "completed":
        return False, f"document {document_id} is already ingested with parser {ctx.parser_version}"
    return True, f"document {document_id} is not yet ingested with parser {ctx.parser_version}"


async def _check_knowledge_import(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    batch_id = _payload_uuid(event, "batch_id")
    _payload_uuid(event, "document_id")
    batch = await uow.knowledge.get_import_batch(batch_id)
    if batch is None:
        return False, f"import batch {batch_id} no longer exists"
    if batch.completed_at is not None:
        return False, f"import batch {batch_id} already completed at {_at(batch.completed_at)}"
    return True, f"import batch {batch_id} has not completed"


async def _check_verification(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    requirement_id = _payload_uuid(event, "requirement_id")
    if await uow.requirements.get(requirement_id) is None:
        return False, f"requirement {requirement_id} no longer exists"
    if await verification_recorded_for_event(requirement_id, event.id):
        return False, f"requirement {requirement_id} is already verified for this event"
    return True, f"requirement {requirement_id} has no verification for this event"


async def _no_consumer(
    uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext
) -> tuple[bool, str]:
    return True, "no consumer acts on this event type"


# The dispatcher's routes (outbox_dispatcher._ROUTES), one check each; a test
# keeps the two sets equal. Anything else goes to the dispatcher's
# placeholder job, which acts on nothing.
_CHECKS: dict[tuple[str, str], _Check] = {
    (AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED): _check_run_created,
    (AGGREGATE_APPROVAL_GATE, EVENT_GATE_DECISION_RECORDED): _check_gate_decision,
    (AGGREGATE_ASYNC_OPERATION, EVENT_OPERATION_REQUESTED): _check_operation,
    (AGGREGATE_KNOWLEDGE_SELECTION, EVENT_KNOWLEDGE_SELECTION_CHANGED): _check_selection_change,
    (AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED): _check_document,
    (AGGREGATE_DOCUMENT, EVENT_KNOWLEDGE_DOCUMENT_UPLOADED): _check_knowledge_import,
    (AGGREGATE_REQUIREMENT, EVENT_REQUIREMENT_VERIFICATION_REQUESTED): _check_verification,
}


async def _assess(uow: UnitOfWork, event: OutboxEvent, ctx: RedriveContext) -> tuple[bool, str]:
    """Whether this existing event may be re-driven, and the reason."""
    if event.status == "pending":
        return False, (
            f"pending, not dead-lettered ({event.attempt_count} of {ctx.max_attempts} attempts "
            "used): the dispatcher will deliver it"
        )
    if event.status == "dispatched":
        return False, f"dispatched at {_at(event.dispatched_at)}: nothing to re-drive"
    check = _CHECKS.get((event.aggregate_type, event.event_type), _no_consumer)
    try:
        return await check(uow, event, ctx)
    except _UnreadablePayload as exc:
        return False, f"payload has no readable {exc.key}: it would dead-letter again"


def _result(
    event_id: uuid.UUID, event: OutboxEvent | None, *, redriven: bool, refused: bool, line: str
) -> RedriveResult:
    return RedriveResult(
        event_id=event_id,
        event_type=event.event_type if event is not None else None,
        status=event.status if event is not None else None,
        redriven=redriven,
        refused=refused,
        line=line,
    )


async def _one(
    event_id: uuid.UUID, ctx: RedriveContext, *, apply: bool, reason: str | None
) -> RedriveResult:
    async with UnitOfWork() as uow:
        event = await uow.outbox.get(event_id)
        if event is None:
            result = _result(event_id, None, redriven=False, refused=True, line=_NO_SUCH_EVENT)
        else:
            allowed, why = await _assess(uow, event, ctx)
            if not allowed:
                line = f"refused: {why}"
                result = _result(event_id, event, redriven=False, refused=True, line=line)
            elif not apply:
                line = f"would re-drive: {why}"
                result = _result(event_id, event, redriven=False, refused=False, line=line)
            elif await uow.outbox.reset_failed(event_id):
                await uow.commit()
                line = f"re-driven: {why}"
                result = _result(event_id, event, redriven=True, refused=False, line=line)
                # The reset's own record: the event, its type and the
                # operator's reason (plan item 20: log only, no audit row).
                logger.info(
                    "outbox event %s (%s) re-driven: reset to pending; reason: %s",
                    event_id,
                    event.event_type,
                    reason,
                )
                return result
            else:
                await uow.session.refresh(event)
                line = f"refused: its status changed to {event.status} meanwhile"
                result = _result(event_id, event, redriven=False, refused=True, line=line)
    logger.info("outbox event %s (%s) %s", event_id, result.event_type, result.line)
    return result


async def redrive(
    event_ids: list[uuid.UUID], ctx: RedriveContext, *, apply: bool, reason: str | None = None
) -> list[RedriveResult]:
    """Check each event and, with `apply`, reset each one that may be
    re-driven, each in its own transaction. A re-drive needs a reason, which
    its log line carries; a dry run writes nothing and needs none."""
    if apply and not (reason and reason.strip()):
        raise ValueError("a re-drive needs a reason")
    return [await _one(event_id, ctx, apply=apply, reason=reason) for event_id in event_ids]


async def list_dead_lettered(ctx: RedriveContext) -> list[RedriveResult]:
    """Every dead-lettered event (up to LIST_LIMIT, oldest first), each with
    the line a dry run would print for it. Writes nothing."""
    async with UnitOfWork() as uow:
        events = await uow.outbox.list_failed(LIST_LIMIT)
        results = []
        for event in events:
            allowed, why = await _assess(uow, event, ctx)
            line = f"would re-drive: {why}" if allowed else f"refused: {why}"
            results.append(_result(event.id, event, redriven=False, refused=not allowed, line=line))
    return results
