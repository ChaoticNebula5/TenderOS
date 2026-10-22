"""Phase 4: reading an async operation's state, and following it live.

GET /operations/{operation_id} returns its status; the ticket and stream
routes push OperationStatusChanged events to the web app the same way a
workflow run's progress is pushed (tenderos_api/outbox_stream.py), so a
screen can keep its button busy until the operation is finished.

GET /tenders/{tender_id}/operations/active?kind=... returns the tender's
queued or running operation of that kind, or null: a screen asks it on
load, so after a reload it can show its button busy and follow the
operation it started before (Phase 4 Stage 2).

Access follows the operation's (or tender's) own organization, with the
same RBAC action as the route that creates that kind of operation; a member
whose role may not create that kind gets 403 ORG_ACCESS_DENIED. A
non-member is answered differently on the two kinds of path:
- /operations/{operation_id}: 404, exactly like a missing operation, so an
  operation id alone does not reveal that another organization's operation
  exists;
- /tenders/{tender_id}/operations/active: 403 ORG_ACCESS_DENIED, like every
  other tender-scoped route (require_permission); a tender that does not
  exist is 404.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.user import User
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_OPERATION_STATUS_CHANGED
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.domain.services import operation_service, tender_service

from tenderos_api.dependencies import get_current_user, get_current_user_for_sse
from tenderos_api.errors import OrgAccessDeniedError
from tenderos_api.outbox_stream import outbox_event_stream
from tenderos_api.schemas.operations import OperationResponse
from tenderos_api.schemas.workflow import SSETicketResponse
from tenderos_api.sse_tickets import SCOPE_OPERATION, TICKET_TTL_SECONDS, mint_sse_ticket

router = APIRouter(tags=["operations"])

# kind -> the RBAC action of the route that creates it (routes/redteam.py's
# _RESOLVE, routes/proposal.py's _EDIT, routes/commercial.py's _RESOLVE).
_KIND_ACTIONS = {
    "redteam_run": "resolve_redteam_finding",
    "proposal_generation": "edit_proposal",
    "commercial_validation": "resolve_commercial_finding",
}


# The ?kind= values the active-operation route accepts (anything else is
# 422): async_operation.OPERATION_KINDS as a type, so FastAPI validates it.
# test_operations_api.py checks the two lists stay equal.
OperationKind = Literal["redteam_run", "proposal_generation", "commercial_validation"]


async def _authorize(organization_id: UUID, kind: str, user: User, not_found: str | None) -> None:
    """The one organization check for every route here. A member whose role
    may not create this kind of operation gets 403. A non-member gets 404
    with `not_found` (the same answer as a missing resource), or, when
    `not_found` is None, 403 ORG_ACCESS_DENIED as tender-scoped routes do."""
    async with UnitOfWork() as uow:
        membership = await uow.memberships.get(organization_id, user.id)
    if membership is None:
        if not_found is None:
            raise OrgAccessDeniedError()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found)
    if Role(membership.role) not in ACTION_ROLES[_KIND_ACTIONS[kind]]:
        raise OrgAccessDeniedError()


async def _authorized_operation(operation_id: UUID, user: User) -> AsyncOperation:
    operation = await operation_service.get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    # Another organization's operation: the same answer as a missing one.
    await _authorize(operation.organization_id, operation.kind, user, "Operation not found")
    return operation


@router.get("/tenders/{tender_id}/operations/active", response_model=OperationResponse | None)
async def get_active_operation(
    tender_id: UUID,
    kind: OperationKind = Query(...),
    current_user: User = Depends(get_current_user),
) -> OperationResponse | None:
    """The tender's queued or running operation of this kind, or null when
    there is none. Read on a screen's load, so a reload does not lose track
    of a run started before it."""
    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    # Another organization's tender: 403, like every other tender-scoped route.
    await _authorize(tender.organization_id, kind, current_user, None)
    active = await operation_service.get_active_operation(tender_id, kind)
    return OperationResponse.from_operation(active) if active is not None else None


@router.get("/operations/{operation_id}", response_model=OperationResponse)
async def get_operation(
    operation_id: UUID, current_user: User = Depends(get_current_user)
) -> OperationResponse:
    operation = await _authorized_operation(operation_id, current_user)
    return OperationResponse.from_operation(operation)


@router.post("/operations/{operation_id}/events/ticket", response_model=SSETicketResponse)
async def mint_operation_events_ticket(
    operation_id: UUID, current_user: User = Depends(get_current_user)
) -> SSETicketResponse:
    """A short-lived, single-use ticket for this operation's stream, the
    same mechanism as the workflow run's (sse_tickets.py): EventSource
    cannot send an Authorization header."""
    await _authorized_operation(operation_id, current_user)
    ticket = await mint_sse_ticket(current_user.id, operation_id, SCOPE_OPERATION)
    return SSETicketResponse(ticket=ticket, expires_in_seconds=TICKET_TTL_SECONDS)


def _operation_message(event: OutboxEvent) -> dict:
    return {
        "event_type": "operation.status_changed",
        "operation_id": event.payload["operation_id"],
        "status": event.payload.get("status"),
        "timestamp": event.created_at.isoformat(),
    }


@router.get("/operations/{operation_id}/events/stream")
async def stream_operation_events(
    operation_id: UUID, current_user: User = Depends(get_current_user_for_sse)
) -> StreamingResponse:
    """Pushes one message per status the operation reaches (a wake-up
    signal: the client re-reads GET /operations/{operation_id})."""
    await _authorized_operation(operation_id, current_user)
    return StreamingResponse(
        outbox_event_stream(
            event_types=[EVENT_OPERATION_STATUS_CHANGED],
            payload_key="operation_id",
            resource_id=operation_id,
            to_message=_operation_message,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )
