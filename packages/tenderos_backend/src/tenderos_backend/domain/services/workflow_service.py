import uuid

from sqlalchemy.exc import IntegrityError

from tenderos_backend.core.enums import WorkflowStatus, coarse_status_for
from tenderos_backend.core.prompts import bundle_version
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.workflow import WorkflowRun, WorkflowTask
from tenderos_backend.db.repositories.workflow import RunNoLongerActiveError
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_WORKFLOW_RUN,
    EVENT_WORKFLOW_RUN_CREATED,
    workflow_run_created_idempotency_key,
    workflow_run_created_payload,
)

_CANCELLABLE_STATUSES = frozenset({"queued", "running", "waiting_for_human"})


_ONE_ACTIVE_RUN_INDEX = "uq_workflow_runs_one_active_per_tender"


def _violated_constraint(exc: IntegrityError) -> str | None:
    """The constraint the database reported, from the driver's own error.
    SQLAlchemy's asyncpg adapter keeps asyncpg's exception as the DBAPI
    error's __cause__, and asyncpg puts the name on it; None when absent."""
    return getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)


class WorkflowRunActiveError(Exception):
    """Phase 4 plan item 43: the tender already has an active run, so a new
    one was refused (uq_workflow_runs_one_active_per_tender). Carries the
    active run's id, so the caller can show that run instead."""

    def __init__(self, active_run_id: uuid.UUID) -> None:
        super().__init__(f"tender already has an active workflow run {active_run_id}")
        self.active_run_id = active_run_id


class DocumentsNotReadyError(Exception):
    """B5: the graph's first real node (requirement_extraction, replacing
    B3's no-op stub) reads every one of the tender's document_chunks in one
    pass - if a document is still 'queued'/'parsing' when that node runs, its
    chunks silently don't exist yet and the extraction agent never sees them,
    directly starving the 0.90 mandatory-recall gate of content it should
    have had. Checked here, at creation, rather than inside the node itself,
    because a node can't usefully "wait" for an in-flight ingestion job
    without tying up a worker slot - refusing the run before it starts is the
    same "don't proceed on a doomed precondition" shape B4's OCR/duplicate
    checks already use.
    """


async def create_workflow_run(
    tender_id: uuid.UUID, organization_id: uuid.UUID, created_by: uuid.UUID
) -> WorkflowRun:
    async with UnitOfWork() as uow:
        if not await uow.documents.all_parsed_for_tender(tender_id):
            raise DocumentsNotReadyError()

        try:
            run = await uow.workflow.create_run(
                tender_id=tender_id,
                prompt_bundle_version=bundle_version(),
                model_config={},
            )
        except IntegrityError as exc:
            # The index refused a second active run (two tabs, or two clicks
            # with their own Idempotency-Keys). No lookup first: the index is
            # the one check, so two concurrent calls cannot both pass it.
            # Any other violation is re-raised as it is (plan item 44): with
            # an active run on the tender, the re-read below would otherwise
            # report it as that run. The loser's insert waits for the
            # winner's commit, so the winner is visible to a fresh read; if
            # none is active any more (it finished in between), the original
            # error stands.
            if _violated_constraint(exc) != _ONE_ACTIVE_RUN_INDEX:
                raise
            await uow.rollback()
            async with UnitOfWork() as reread_uow:
                active = await reread_uow.workflow.get_active_run_for_tender(tender_id)
            if active is None:
                raise
            raise WorkflowRunActiveError(active.id) from None
        await uow.outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run.id,
            event_type=EVENT_WORKFLOW_RUN_CREATED,
            payload=workflow_run_created_payload(run.id, tender_id),
            idempotency_key=workflow_run_created_idempotency_key(run.id),
        )
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(created_by),
            action="create_workflow_run",
            tender_id=tender_id,
            workflow_run_id=run.id,
            target_type="workflow_run",
            target_id=run.id,
        )
        await uow.commit()
        return run


async def get_workflow_run(run_id: uuid.UUID) -> WorkflowRun | None:
    async with UnitOfWork() as uow:
        return await uow.workflow.get_run(run_id)


async def list_workflow_runs(tender_id: uuid.UUID) -> list[WorkflowRun]:
    async with UnitOfWork() as uow:
        return await uow.workflow.list_runs_for_tender(tender_id)


async def list_tasks(run_id: uuid.UUID) -> list[WorkflowTask]:
    async with UnitOfWork() as uow:
        return await uow.workflow.list_tasks(run_id)


async def list_events(run_id: uuid.UUID) -> list[AuditEvent]:
    async with UnitOfWork() as uow:
        return await uow.audit.list_for_workflow_run(run_id)


async def claim_run_for_start(run_id: uuid.UUID) -> bool:
    """Claim a queued run for its first graph invocation (queued -> running,
    WorkflowRepository.claim_queued_run). Returns False when the run is
    already running or past it, cancelled, or gone: the caller must not
    invoke the graph then."""
    async with UnitOfWork() as uow:
        claimed = await uow.workflow.claim_queued_run(run_id)
        await uow.commit()
    return claimed


async def claim_run_for_resume(run_id: uuid.UUID) -> bool:
    """Claim a run waiting at a gate for its resume (waiting_for_human ->
    running, WorkflowRepository.claim_waiting_run). Returns False when the
    run is no longer waiting: the caller must not resume then."""
    async with UnitOfWork() as uow:
        claimed = await uow.workflow.claim_waiting_run(run_id)
        await uow.commit()
    return claimed


async def is_cancelled(run_id: uuid.UUID) -> bool:
    """I6 enforcement: checked at every point a paused/idle run is about to
    make forward progress again - the two job-queue entry points
    (run_workflow/resume_workflow, before invoking the graph at all),
    specialist_dispatch (before Send()-ing new tasks), and the gate nodes
    (before opening a new approval_gates row). A run missing entirely is
    treated as not cancelled - callers that reach this already hold a real
    run_id from a row they just loaded.
    """
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    return run is not None and run.status == "cancelled"


async def sync_run_status(run_id: uuid.UUID, status: WorkflowStatus) -> None:
    """Called by orchestration nodes after they change TenderState["status"].

    Translates the graph's fine-grained status to workflow_runs' coarse column
    (core.enums.coarse_status_for) and writes it - the one place "workflow status
    synchronization" (its own B3 Build-list item) actually happens, so nodes don't
    each re-implement the same open-UoW-and-write dance.

    Plan item 44: a run already completed, failed or cancelled keeps its
    status (WorkflowRepository.update_run_status refuses in its UPDATE), and
    RunNoLongerActiveError stops the calling node; the workflow jobs catch
    it and return quietly. Before, a node still running in a cancelled run's
    job could write a non-terminal status over "cancelled" and revive it.
    """
    async with UnitOfWork() as uow:
        written = await uow.workflow.update_run_status(run_id, coarse_status_for(status))
        await uow.commit()
    if not written:
        raise RunNoLongerActiveError(run_id, f"status {status!r}")


async def mark_run_failed_on_timeout(run_id: uuid.UUID) -> None:
    """B17: called only by the synchronous RQ job wrapper
    (apps/worker/src/tenderos_worker/jobs/workflow.py) when RQ's own
    rq.timeouts.JobTimeoutException kills a run mid-flight - the one
    failure mode run_workflow/resume_workflow/retry_workflow_run's own
    try/except can never see. RQ's death-penalty signal interrupts
    asyncio's event loop itself while it's polling for I/O (confirmed
    directly against a real stuck run: the traceback bottoms out inside
    asyncio's own _run_once/selectors.select, not at any awaited line
    inside those coroutines' own try blocks), so their internal `except
    Exception` never runs and workflow_runs.status is left at whatever
    non-terminal value it last had, forever - see KNOWN_LIMITATIONS.md's
    own entry for the live run that exposed this (status stuck at
    "running", completed_at NULL, retry_count 0, confirmed by querying the
    row directly).

    Distinct from a normal graph failure specifically so the two are
    distinguishable after the fact, not both silently filed under the same
    undifferentiated "failed": a normal failure (run_workflow's own except
    block) writes no audit event describing why at all, so any audit event
    at run-failure time is, by construction, unique to this path.

    Guarded by mark_failed_if_not_terminal, not update_run_status: a
    cancel_workflow_run call can land between when a run started and when
    RQ's death penalty fires, and this must never overwrite that "cancelled"
    with "failed" (or back the overwrite with a misleading timeout audit
    event) just because it observed a stale, since-superseded status.
    """
    async with UnitOfWork() as uow:
        moved = await uow.workflow.mark_failed_if_not_terminal(run_id)
        if not moved:
            return
        run = await uow.workflow.get_run(run_id)
        if run is None:
            return
        tender = await uow.tenders.get(run.tender_id)
        if tender is not None:
            await uow.audit.create(
                organization_id=tender.organization_id,
                actor_type="system",
                actor_id="rq_death_penalty",
                action="workflow_run_timed_out",
                tender_id=run.tender_id,
                workflow_run_id=run_id,
                target_type="workflow_run",
                target_id=run_id,
                metadata={"error_code": "job_timeout"},
            )
        await uow.commit()


async def cancel_workflow_run(
    run_id: uuid.UUID, organization_id: uuid.UUID, actor_user_id: uuid.UUID
) -> WorkflowRun | None:
    """Per I6: mark cancelled, block new tasks, never touch what's already written.

    Cancelling a run that's already completed/failed/cancelled is a no-op (idempotent
    repeat), not an error - I6 never describes a rejection case for this, and treating
    it as an error would make a retried cancel request unsafe to repeat.
    """
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        if run is None:
            return None
        if run.status in _CANCELLABLE_STATUSES:
            if not await uow.workflow.update_run_status(run_id, "cancelled"):
                # It finished between the read and the write: nothing to
                # cancel (an idempotent no-op, as for a finished run).
                await uow.rollback()
                return await get_workflow_run(run_id)
            # In the same transaction (plan item 44): a gate left pending on
            # a cancelled run could still be decided.
            await uow.gates.close_pending_for_run(run_id)
            await uow.audit.create(
                organization_id=organization_id,
                actor_type="user",
                actor_id=str(actor_user_id),
                action="cancel_workflow_run",
                tender_id=run.tender_id,
                workflow_run_id=run.id,
                target_type="workflow_run",
                target_id=run.id,
            )
            await uow.commit()
            return await uow.workflow.get_run(run_id)
        return run
