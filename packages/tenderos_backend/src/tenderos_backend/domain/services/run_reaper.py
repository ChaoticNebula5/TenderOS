"""The workflow run reaper: fail a run whose job will never move it on.

Phase 4 plan item 19 (decision 0035, as redesigned). Item 44's rule: fail
work that is lost, never work that is only waiting. A run waiting behind
another tender's 30-minute job on the one workflow worker, or waiting for a
scheduled retry, is waiting; a run whose worker was killed mid-job, whose
work horse died, or whose retry Redis lost, is lost. Before this, a lost run
stayed queued or running forever, and since item 43 its tender could not
start another.

Each run records the RQ job now responsible for it (current_job_id, written
by the claims and by retry scheduling). The outbox dispatcher, which owns
the Redis connection, asks RQ about that job (RunJobInspector in
outbox_dispatcher.py) about once a minute, and this module decides and
writes:

- running: its current job is in RQ's failed, canceled or finished registry,
  unknown to RQ for more than UNKNOWN_JOB_GRACE after it was recorded, or
  abandoned (its started entry past due on two looks two minutes apart):
  WORKER_LOST.
- queued, with its WorkflowRunCreated dispatched: its start job is failed,
  canceled, finished, unknown or abandoned: DISPATCH_FAILED. Dead-lettered
  instead: failed by the dispatcher in the dead-letter's own transaction
  (fail_dead_lettered_run).
- waiting_for_human, latest gate decided, its GateDecisionRecorded
  dispatched, and the resume job never claimed the run (it is not the
  current job): that resume job is failed, canceled, finished, unknown or
  abandoned: DISPATCH_FAILED. A resume that did claim the run and left it
  waiting is never reaped, and neither is a waiting run from before
  migration 024, which has no current job to tell the two apart. (Until
  plan item 71 a "changes_requested" decision left its run waiting this
  way, on purpose; since migration 029 it ends the run with the terminal
  status changes_requested, which no rule here lists.)
- queued or running, past MAX_RUN_JOB_AGE whatever RQ says: the backstop.

Every fail is one conditional UPDATE naming the status and current job that
were checked (WorkflowRepository.fail_lost_run), so it cannot race a claim
or a newly recorded retry, and never moves a run out of a terminal status.
Nothing is re-run: that would repeat paid LLM calls; the user starts
processing again, from the beginning.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import RUN_DISPATCH_FAILED, RUN_WORKER_LOST
from tenderos_backend.db.repositories.workflow import RunToCheck
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_WORKFLOW_RUN_CREATED,
)
from tenderos_backend.domain.services.workflow_service import resume_job_id, start_job_id

logger = logging.getLogger(__name__)

# What RQ says about a job that will never move its run on: the verdicts the
# dispatcher's RunJobInspector returns (None means it may still run). Each is
# also the `rule` recorded in the run's workflow_run_reaped audit event.
JOB_FAILED = "failed_registry"
JOB_CANCELED = "canceled"
JOB_FINISHED = "finished"
JOB_UNKNOWN = "unknown"
JOB_ABANDONED = "abandoned"
# The two rules that are not RQ verdicts.
RULE_CAP = "cap"
RULE_DEAD_LETTER = "dead_letter"

# Design numbers, not measured.
#
# The cap must never fire on a working system. A current job's longest
# legitimate life is its retry delay (at most 15 minutes, the clamp in
# orchestration/retry.py), plus waiting behind every job ahead of it on the
# one workflow worker (up to 1800 s each; RQ kills a horse by 1860 s), plus
# its own run (at most 1860 s). 6 hours is about 11 maximum-length jobs
# ahead, 16 times the outbox's longest retry span (22.5 min), or a
# several-hour outage, and it bounds what only the cap catches (a retry the
# scheduler never promotes, a run from before migration 024 with no job
# recorded) to 6 hours instead of forever. The same number as item 44's
# operations cap, for the same reasons.
MAX_RUN_JOB_AGE = timedelta(hours=6)
# A retry's id is recorded (and committed) before it is enqueued, so for a
# moment it names a job RQ does not know yet. A minute covers that.
UNKNOWN_JOB_GRACE = timedelta(seconds=60)

JobVerdict = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class ReapedRun:
    run_id: uuid.UUID
    error_code: str
    rule: str
    job_id: str | None


def _lost_rule(verdict: str | None, run: RunToCheck) -> str | None:
    """The rule a running or queued run is reaped under, or None to leave
    it. A job unknown to RQ is lost only once its record is past the grace;
    a queued run's start job is enqueued before its event is marked
    dispatched, so it has no grace (job_age None)."""
    if verdict is None:
        return None
    if verdict == JOB_UNKNOWN and run.job_age is not None and run.job_age <= UNKNOWN_JOB_GRACE:
        return None
    return verdict


async def reap_lost_runs(batch_size: int, job_verdict: JobVerdict) -> list[ReapedRun]:
    """Fail the queued, running and gate-waiting runs whose job RQ says
    will never move them on, at most batch_size of each kind, each with its
    status event and audit event. Any error from `job_verdict` (Redis
    unreachable) propagates before anything is written; the dispatcher logs
    it, and the cap still runs."""
    async with UnitOfWork() as uow:
        runs = await uow.workflow.list_runs_to_check(batch_size)
        at_gates = await uow.workflow.list_waiting_runs_with_a_decided_gate(batch_size)

    reaped: list[ReapedRun] = []
    for run in runs:
        if run.status == "running":
            job_id, code = run.current_job_id, RUN_WORKER_LOST
        else:
            job_id, code = start_job_id(run.run_id), RUN_DISPATCH_FAILED
        if job_id is None:
            continue
        rule = _lost_rule(await job_verdict(job_id), run)
        if rule is None:
            continue
        if await _fail(run.run_id, run.status, run.current_job_id, code, rule, job_id):
            reaped.append(ReapedRun(run.run_id, code, rule, job_id))

    for at_gate in at_gates:
        job_id = resume_job_id(at_gate.run_id, at_gate.gate_id)
        if at_gate.current_job_id == job_id:
            # The resume claimed the run, so whatever left it waiting since
            # is its outcome, not a loss. (A changes_requested decision did,
            # until plan item 71 made it a terminal status.)
            continue
        rule = await job_verdict(job_id)
        if rule is None:
            continue
        if await _fail(
            at_gate.run_id,
            "waiting_for_human",
            at_gate.current_job_id,
            RUN_DISPATCH_FAILED,
            rule,
            job_id,
        ):
            reaped.append(ReapedRun(at_gate.run_id, RUN_DISPATCH_FAILED, rule, job_id))
    return reaped


async def reap_runs_past_cap(batch_size: int) -> list[ReapedRun]:
    """Fail queued runs created, and running runs whose current job was
    recorded, more than MAX_RUN_JOB_AGE ago, whatever RQ says, at most
    batch_size of each. Needs no Redis, so it runs even when the RQ rules
    cannot."""
    async with UnitOfWork() as uow:
        runs = await uow.workflow.list_runs_past_cap(MAX_RUN_JOB_AGE, batch_size)

    reaped: list[ReapedRun] = []
    for run in runs:
        if run.status == "running":
            job_id, code = run.current_job_id, RUN_WORKER_LOST
        else:
            job_id, code = start_job_id(run.run_id), RUN_DISPATCH_FAILED
        if await _fail(run.run_id, run.status, run.current_job_id, code, RULE_CAP, job_id):
            reaped.append(ReapedRun(run.run_id, code, RULE_CAP, job_id))
    return reaped


async def fail_dead_lettered_run(uow: UnitOfWork, event: OutboxEvent) -> ReapedRun | None:
    """A dead-lettered WorkflowRunCreated never starts its run, and a
    dead-lettered GateDecisionRecorded never resumes it: the run would stay
    queued, or waiting at a decided gate, for good, holding its tender's one
    active run. Fail it as DISPATCH_FAILED here, inside the dead-letter's own
    transaction (the caller commits), only while it is still in the status
    the event was meant to move it from. Any other event, or a payload with
    no readable run id, does nothing: raising would roll back the
    dead-letter and retry the event forever."""
    route = (event.aggregate_type, event.event_type)
    if route == (AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED):
        status_seen = "queued"
    elif route == (AGGREGATE_APPROVAL_GATE, EVENT_GATE_DECISION_RECORDED):
        status_seen = "waiting_for_human"
    else:
        return None
    try:
        run_id = uuid.UUID(str(event.payload["workflow_run_id"]))
    except (KeyError, TypeError, ValueError):
        logger.error(
            "outbox event %s: dead-lettered %s has no readable workflow_run_id; "
            "the run reaper's cap will fail its run",
            event.id,
            event.event_type,
        )
        return None
    if status_seen == "queued":
        job_id: str | None = start_job_id(run_id)
    else:
        gate_id = event.payload.get("gate_id")
        job_id = resume_job_id(run_id, gate_id) if gate_id else None

    run = await uow.workflow.get_run(run_id)
    if run is None or run.status != status_seen:
        return None
    if await _fail_in(
        uow, run_id, status_seen, run.current_job_id, RUN_DISPATCH_FAILED, RULE_DEAD_LETTER, job_id
    ):
        return ReapedRun(run_id, RUN_DISPATCH_FAILED, RULE_DEAD_LETTER, job_id)
    return None


async def _fail(
    run_id: uuid.UUID,
    status_seen: str,
    job_id_seen: str | None,
    error_code: str,
    rule: str,
    job_id: str | None,
) -> bool:
    async with UnitOfWork() as uow:
        if not await _fail_in(uow, run_id, status_seen, job_id_seen, error_code, rule, job_id):
            return False
        await uow.commit()
    return True


async def _fail_in(
    uow: UnitOfWork,
    run_id: uuid.UUID,
    status_seen: str,
    job_id_seen: str | None,
    error_code: str,
    rule: str,
    job_id: str | None,
) -> bool:
    """The conditional fail, then, only when it matched, the audit event
    saying why, in the caller's transaction."""
    if not await uow.workflow.fail_lost_run(run_id, status_seen, job_id_seen, error_code):
        return False
    run = await uow.workflow.get_run(run_id)
    tender = await uow.tenders.get(run.tender_id) if run is not None else None
    if run is not None and tender is not None:
        await uow.audit.create(
            organization_id=tender.organization_id,
            actor_type="system",
            actor_id="run_reaper",
            action="workflow_run_reaped",
            tender_id=run.tender_id,
            workflow_run_id=run_id,
            target_type="workflow_run",
            target_id=run_id,
            metadata={"error_code": error_code, "rule": rule, "job_id": job_id},
        )
    return True
