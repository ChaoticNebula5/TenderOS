import uuid

from tenderos_backend.core.enums import WorkflowStatus, coarse_status_for
from tenderos_backend.core.prompts import bundle_version
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.workflow import WorkflowRun, WorkflowTask
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_WORKFLOW_RUN,
    EVENT_WORKFLOW_RUN_CREATED,
    workflow_run_created_idempotency_key,
    workflow_run_created_payload,
)

_CANCELLABLE_STATUSES = frozenset({"queued", "running", "waiting_for_human"})


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

        run = await uow.workflow.create_run(
            tender_id=tender_id,
            prompt_bundle_version=bundle_version(),
            model_config={},
        )
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


async def list_tasks(run_id: uuid.UUID) -> list[WorkflowTask]:
    async with UnitOfWork() as uow:
        return await uow.workflow.list_tasks(run_id)


async def list_events(run_id: uuid.UUID) -> list[AuditEvent]:
    async with UnitOfWork() as uow:
        return await uow.audit.list_for_workflow_run(run_id)


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
    """
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run_id, coarse_status_for(status))
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
            await uow.workflow.update_run_status(run_id, "cancelled")
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
