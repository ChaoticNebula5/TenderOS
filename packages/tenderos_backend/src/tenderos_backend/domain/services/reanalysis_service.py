"""The duplicate guard for reanalyze_knowledge_selection_change (plan item
18, decision 0036).

The outbox delivers KnowledgeSelectionChanged at least once (decision
0029). The job claims its selection change here before any work; a second
delivery of the same change gets no claim and does nothing. Each call is
its own short transaction, so the claim is visible to a concurrent
delivery before the job's long work starts.

Plan item 124: a claim also ends failed, for good. The job fails its own
claim when it cannot finish (fail_reanalysis), and the reaper below fails
the claim of a job RQ says will never finish it, or whose lease lapsed
(reap_lost_reanalyses, reap_lapsed_reanalyses; the dispatcher calls both
once a minute).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from tenderos_backend.db.models.reanalysis_claim import (
    REANALYSIS_RULE_LEASE,
    REANALYSIS_WORKER_LOST,
)
from tenderos_backend.db.repositories.reanalysis_claims import RunningClaim
from tenderos_backend.db.unit_of_work import UnitOfWork

# Design number, not measured. The reanalysis job runs on the workflow
# queue, whose RQ job timeout is 1800 s (the worker's
# WORKFLOW_JOB_TIMEOUT_SECONDS), so a claim's job is dead by then; plus the
# same 60 s margin operations use (operation_service.LEASE_MARGIN). The lease
# starts when the job claims, not when it was enqueued, so time spent
# waiting in the queue behind other jobs never uses it up (plan item 44's
# rule: waiting is not lost). tests/unit/test_reanalysis_lease.py pins this
# to the worker's timeout.
REANALYSIS_LEASE = timedelta(seconds=1800 + 60)

# Plan item 124 (user decision Q1, 2026-11-27): the waits between the job's
# attempts at projecting and previewing when the graph moved on meanwhile,
# made to outlast a person's burst of changes rather than a read's jitter: 5
# attempts, 37 s of waiting in all, well inside the job's 1800 s timeout and
# the lease. Design numbers, not measured.
STALE_PREVIEW_WAITS_SECONDS = (2.0, 5.0, 10.0, 20.0)


async def claim_reanalysis(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, state_version: int
) -> uuid.UUID | None:
    """Claim this selection change for the calling delivery. Returns the
    claim token to complete it with, or None when another delivery holds
    or completed it: the caller must then do nothing.

    A new change is inserted; an existing one is taken over only if it is
    still running with a lapsed lease (its job died). Committed before
    returning."""
    async with UnitOfWork() as uow:
        token = await uow.reanalysis_claims.claim(
            tender_id, knowledge_item_id, state_version, REANALYSIS_LEASE
        )
        if token is None:
            token = await uow.reanalysis_claims.take_over_expired(
                tender_id, knowledge_item_id, state_version, REANALYSIS_LEASE
            )
        await uow.commit()
    return token


async def claim_id(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, state_version: int
) -> uuid.UUID | None:
    """The id of this selection change's claim row (PR 29): the reanalysis
    job's LLM calls are recorded under it (agent_traces.reanalysis_claim_id).
    Looked up by the claim's own unique key (uq_reanalysis_claim) rather than
    returned by claim_reanalysis, whose token-only contract its callers and
    tests rely on."""
    async with UnitOfWork() as uow:
        return await uow.reanalysis_claims.id_for(tender_id, knowledge_item_id, state_version)


async def complete_reanalysis(claim_token: uuid.UUID) -> bool:
    """Mark the claim completed. False when the token is no longer current
    (another delivery took the claim over) or the claim is no longer
    running (the reaper ended it, plan item 124)."""
    async with UnitOfWork() as uow:
        completed = await uow.reanalysis_claims.complete(claim_token)
        await uow.commit()
    return completed


async def fail_reanalysis(claim_token: uuid.UUID, error_code: str) -> bool:
    """Plan item 124: the job ends its own claim failed, in its own
    committed transaction. False when the token is no longer current or the
    claim no longer running; then the claim is left as it is."""
    async with UnitOfWork() as uow:
        failed = await uow.reanalysis_claims.fail(claim_token, error_code)
        await uow.commit()
    return failed


def reanalysis_job_id(tender_id: uuid.UUID | str, state_version: int) -> str:
    """The RQ job id of a selection change's reanalysis job: the dispatcher
    enqueues it under this id (outbox_dispatcher's route), and the reaper
    asks RQ about it. One function, so the two cannot drift. Underscores,
    not colons: RQ's job id constraint."""
    return f"reanalyze_{tender_id}_{state_version}"


# --- the reaper (plan item 124) ---------------------------------------------
#
# Item 44's rule, as for runs and operations: fail work that is lost, never
# work that is only waiting. A running claim is lost when RQ says its job
# will never finish it (failed, canceled, finished, unknown, or abandoned on
# two looks; outbox_dispatcher.RunJobInspector, the run reaper's own, since
# reanalysis jobs are on the same workflow queue), or when its lease lapsed
# (RQ kills a job by its timeout, and the lease is that plus a margin from
# after the job started: no Redis needed). No grace for "unknown": the job
# inserts its own claim while it runs, so its job hash exists for as long as
# the claim can legitimately be running. Nothing is re-run: that would repeat
# paid compliance calls; the person re-runs it with two selection changes, as
# readiness's reanalysis_not_applied blocker says.

JobVerdict = Callable[[str], Awaitable[str | None]]


@dataclass(frozen=True)
class ReapedClaim:
    claim_id: uuid.UUID
    error_code: str
    rule: str
    job_id: str


async def _reap(claim: RunningClaim, rule: str) -> ReapedClaim | None:
    async with UnitOfWork() as uow:
        reaped = await uow.reanalysis_claims.reap(
            claim.claim_id, claim.claim_token, REANALYSIS_WORKER_LOST, rule
        )
        await uow.commit()
    if not reaped:
        return None
    job_id = reanalysis_job_id(claim.tender_id, claim.state_version)
    return ReapedClaim(claim.claim_id, REANALYSIS_WORKER_LOST, rule, job_id)


async def reap_lost_reanalyses(batch_size: int, job_verdict: JobVerdict) -> list[ReapedClaim]:
    """Fail running claims whose job RQ says will never finish them, at
    most batch_size, oldest first, each in its own transaction. Any error
    from job_verdict (Redis unreachable) propagates: nothing is failed on
    that pass, and reap_lapsed_reanalyses still runs."""
    async with UnitOfWork() as uow:
        claims = await uow.reanalysis_claims.list_running(batch_size)
    reaped: list[ReapedClaim] = []
    for claim in claims:
        verdict = await job_verdict(reanalysis_job_id(claim.tender_id, claim.state_version))
        if verdict is None:
            continue
        ended = await _reap(claim, verdict)
        if ended is not None:
            reaped.append(ended)
    return reaped


async def reap_lapsed_reanalyses(batch_size: int) -> list[ReapedClaim]:
    """Fail running claims whose lease lapsed, whatever RQ says (rule
    "lease"), at most batch_size, oldest first. Needs no Redis, so it runs
    even when the RQ rule cannot."""
    async with UnitOfWork() as uow:
        claims = await uow.reanalysis_claims.list_running(batch_size)
    reaped: list[ReapedClaim] = []
    for claim in claims:
        if not claim.lease_lapsed:
            continue
        ended = await _reap(claim, REANALYSIS_RULE_LEASE)
        if ended is not None:
            reaped.append(ended)
    return reaped
