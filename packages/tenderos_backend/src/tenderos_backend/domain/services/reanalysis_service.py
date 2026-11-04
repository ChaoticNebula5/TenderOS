"""The duplicate guard for reanalyze_knowledge_selection_change (plan item
18, decision 0036).

The outbox delivers KnowledgeSelectionChanged at least once (decision
0029). The job claims its selection change here before any work; a second
delivery of the same change gets no claim and does nothing. Each call is
its own short transaction, so the claim is visible to a concurrent
delivery before the job's long work starts.
"""

import uuid
from datetime import timedelta

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


async def complete_reanalysis(claim_token: uuid.UUID) -> bool:
    """Mark the claim completed. False when the token is no longer current
    (the lease lapsed and another delivery took the claim over)."""
    async with UnitOfWork() as uow:
        completed = await uow.reanalysis_claims.complete(claim_token)
        await uow.commit()
    return completed
