from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import tender_service, workflow_service

from tenderos_api.dependencies import require_permission
from tenderos_api.errors import DocumentsNotReadyError
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.workflow import (
    AuditEventResponse,
    WorkflowRunResponse,
    WorkflowTaskResponse,
)

router = APIRouter(tags=["workflow"])


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
    response = WorkflowRunResponse.model_validate(run, from_attributes=True)
    await idempotency.finalize(202, response.model_dump(mode="json"))
    return response


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: UUID, current_user: User = Depends(require_permission("create_bid"))
) -> WorkflowRunResponse:
    run = await workflow_service.get_workflow_run(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow run not found")
    return WorkflowRunResponse.model_validate(run, from_attributes=True)


@router.get("/workflow-runs/{run_id}/tasks", response_model=list[WorkflowTaskResponse])
async def list_workflow_tasks(
    run_id: UUID, current_user: User = Depends(require_permission("create_bid"))
) -> list[WorkflowTaskResponse]:
    tasks = await workflow_service.list_tasks(run_id)
    return [WorkflowTaskResponse.model_validate(t, from_attributes=True) for t in tasks]


@router.get("/workflow-runs/{run_id}/events", response_model=list[AuditEventResponse])
async def list_workflow_events(
    run_id: UUID, current_user: User = Depends(require_permission("create_bid"))
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
