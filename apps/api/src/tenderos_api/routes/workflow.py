from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import tender_service, workflow_service

from tenderos_api.dependencies import require_permission, require_permission_for_sse
from tenderos_api.errors import DocumentsNotReadyError, WorkflowRunActiveAPIError
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.outbox_stream import open_outbox_event_stream
from tenderos_api.schemas.workflow import (
    AuditEventResponse,
    SSETicketResponse,
    WorkflowRunResponse,
    WorkflowTaskResponse,
)
from tenderos_api.sse_tickets import SCOPE_WORKFLOW_RUN, TICKET_TTL_SECONDS, mint_sse_ticket

router = APIRouter(tags=["workflow"])

# The 4 outbox event types db/repositories/workflow.py's own choke points
# write (see that module's docstring) - the wake-up signals this stream
# polls the outbox for. Not the same vocabulary GET .../events (audit_events)
# serves - that route is untouched, out of B14's scope (KNOWN_LIMITATIONS.md).
_WORKFLOW_PROGRESS_EVENT_TYPES = (
    "WorkflowRunStatusChanged",
    "WorkflowTaskCreated",
    "WorkflowTaskCompleted",
    "WorkflowTaskFailed",
)

# G13's own wire vocabulary (TenderOS-Build-Guide.md:2640-2650) is dotted-
# lowercase ("task.completed"); this codebase's internal outbox_events.py
# constants are PascalCase, matching every other event in that file. This
# map is the one place that translates between the two - the outbox's own
# event_type column stays consistent with its neighbors, and the wire
# format stays exactly what G13 locks.
_WIRE_EVENT_TYPE = {
    "WorkflowRunStatusChanged": "run.status_changed",
    "WorkflowTaskCreated": "task.created",
    "WorkflowTaskCompleted": "task.completed",
    "WorkflowTaskFailed": "task.failed",
}


def _workflow_message(event: OutboxEvent) -> dict:
    return {
        "event_type": _WIRE_EVENT_TYPE[event.event_type],
        "workflow_run_id": event.payload["workflow_run_id"],
        "task_id": event.payload.get("task_id"),
        "task_type": event.payload.get("task_type"),
        "timestamp": event.created_at.isoformat(),
    }


async def _workflow_progress_stream(run_id: UUID) -> AsyncIterator[str]:
    """The run's progress stream: the four workflow event types for this
    run, as G13's dotted wire names. The polling loop itself lives in
    tenderos_api/outbox_stream.py (moved there in Phase 4 so async
    operations stream the same way); see its docstring for the transport
    and for why the client re-reads state rather than trusting the payload.

    Deliberately bypasses apps/worker/outbox_dispatcher.py's own, separate
    POLL_INTERVAL_SECONDS = 5 tick - that poll decides which events need an
    RQ job, a different purpose, in a different process (the worker
    container, not this API process). Routing through it would add a
    second, unnecessary cross-process hop on top of its own 5s floor, real
    latency this stream's "no manual reload" purpose can't absorb.

    Awaited by the route before the response starts: plan item 54's
    starting set of event ids is read here (see open_outbox_event_stream).
    """
    return await open_outbox_event_stream(
        event_types=_WORKFLOW_PROGRESS_EVENT_TYPES,
        payload_key="workflow_run_id",
        resource_id=run_id,
        to_message=_workflow_message,
    )


@router.get("/workflow-runs/{run_id}/events/stream")
async def stream_workflow_events(
    run_id: UUID, current_user: User = Depends(require_permission_for_sse("view_workflow"))
) -> StreamingResponse:
    return StreamingResponse(
        await _workflow_progress_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


# Not in Part G's locked route list - added code-first, same precedent as
# GET /employees/{id} (KNOWN_LIMITATIONS.md): a real gap found building
# against the spec, not a documented capability implemented late.
@router.post("/workflow-runs/{run_id}/events/ticket", response_model=SSETicketResponse)
async def mint_workflow_events_ticket(
    run_id: UUID, current_user: User = Depends(require_permission("view_workflow"))
) -> SSETicketResponse:
    """B15: mints the short-lived, single-use ticket GET .../events/stream
    accepts in place of the real access JWT - see sse_tickets.py's own
    module docstring for why, and KNOWN_LIMITATIONS.md's B14/B15 SSE-auth
    entries for the history.

    Same view_workflow action as the stream route itself and every other
    plain GET on this run, not a new action - deliberately, not by
    reflex: minting answers the identical "can this user watch this run's
    progress" question the stream route's own require_permission_for_sse
    already asks, just once, up front, instead of on every reconnect. A
    dedicated action would gate that one question under two different
    names for no real distinction.
    """
    ticket = await mint_sse_ticket(current_user.id, run_id, SCOPE_WORKFLOW_RUN)
    return SSETicketResponse(ticket=ticket, expires_in_seconds=TICKET_TTL_SECONDS)


@router.post(
    "/tenders/{tender_id}/workflow-runs", response_model=WorkflowRunResponse, status_code=202
)
async def create_workflow_run(
    tender_id: UUID,
    current_user: User = Depends(require_permission("create_bid")),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("create_workflow_run", required=True)
    ),
) -> WorkflowRunResponse:
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return WorkflowRunResponse.model_validate(cached_body)

    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    try:
        run = await workflow_service.create_workflow_run(
            tender_id=tender_id, organization_id=tender.organization_id, created_by=current_user.id
        )
    except workflow_service.DocumentsNotReadyError as exc:
        raise DocumentsNotReadyError() from exc
    except workflow_service.WorkflowRunActiveError as exc:
        raise WorkflowRunActiveAPIError(str(exc.active_run_id)) from exc
    response = WorkflowRunResponse.model_validate(run, from_attributes=True)
    await idempotency.finalize(202, response.model_dump(mode="json"))
    return response


@router.get("/tenders/{tender_id}/workflow-runs", response_model=list[WorkflowRunResponse])
async def list_workflow_runs(
    tender_id: UUID, current_user: User = Depends(require_permission("view_workflow"))
) -> list[WorkflowRunResponse]:
    runs = await workflow_service.list_workflow_runs(tender_id)
    return [WorkflowRunResponse.model_validate(r, from_attributes=True) for r in runs]


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: UUID, current_user: User = Depends(require_permission("view_workflow"))
) -> WorkflowRunResponse:
    run = await workflow_service.get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found")
    return WorkflowRunResponse.model_validate(run, from_attributes=True)


@router.get("/workflow-runs/{run_id}/tasks", response_model=list[WorkflowTaskResponse])
async def list_workflow_tasks(
    run_id: UUID, current_user: User = Depends(require_permission("view_workflow"))
) -> list[WorkflowTaskResponse]:
    tasks = await workflow_service.list_tasks(run_id)
    return [WorkflowTaskResponse.model_validate(t, from_attributes=True) for t in tasks]


@router.get("/workflow-runs/{run_id}/events", response_model=list[AuditEventResponse])
async def list_workflow_events(
    run_id: UUID, current_user: User = Depends(require_permission("view_workflow"))
) -> list[AuditEventResponse]:
    events = await workflow_service.list_events(run_id)
    return [AuditEventResponse.model_validate(e, from_attributes=True) for e in events]


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunResponse)
async def cancel_workflow_run(
    run_id: UUID, current_user: User = Depends(require_permission("cancel_workflow_run"))
) -> WorkflowRunResponse:
    run = await workflow_service.get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found")
    tender = await tender_service.get_tender(run.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    cancelled = await workflow_service.cancel_workflow_run(
        run_id, tender.organization_id, current_user.id
    )
    if cancelled is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found")
    return WorkflowRunResponse.model_validate(cancelled, from_attributes=True)
