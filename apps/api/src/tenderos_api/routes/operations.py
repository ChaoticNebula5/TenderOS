"""Phase 4: reading an async operation's state, and following it live.

GET /operations/{operation_id} returns its status; the ticket and stream
routes push OperationStatusChanged events to the web app the same way a
workflow run's progress is pushed (tenderos_api/outbox_stream.py), so a
screen can keep its button busy until the operation is finished.

Access follows the operation's own organization, with the same RBAC action
as the route that creates that kind of operation. An operation that does
not exist and one belonging to another organization both answer 404, so
the response does not reveal that another organization's operation exists.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.user import User
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_OPERATION_STATUS_CHANGED
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.domain.services import operation_service

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


async def _authorized_operation(operation_id: UUID, user: User) -> AsyncOperation:
    operation = await operation_service.get_operation(operation_id)
    if operation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    async with UnitOfWork() as uow:
        membership = await uow.memberships.get(operation.organization_id, user.id)
    if membership is None:
        # Another organization's operation: the same answer as a missing one.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operation not found")
    if Role(membership.role) not in ACTION_ROLES[_KIND_ACTIONS[operation.kind]]:
        raise OrgAccessDeniedError()
    return operation


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
