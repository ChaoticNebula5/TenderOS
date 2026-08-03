from uuid import UUID

from fastapi import APIRouter, Depends
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import membership_service, tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.errors import AlreadyMemberError, UserNotFoundError
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.memberships import AddMembershipRequest, MembershipResponse
from tenderos_api.schemas.tenders import TenderCreateRequest, TenderResponse

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.post("/{org_id}/tenders", response_model=TenderResponse, status_code=201)
async def create_tender(
    org_id: UUID,
    body: TenderCreateRequest,
    current_user: User = Depends(require_permission("create_bid")),
    idempotency: IdempotencyContext = Depends(get_idempotency_context("create_tender")),
) -> TenderResponse:
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return TenderResponse.model_validate(cached_body)

    tender = await tender_service.create_tender(
        organization_id=org_id,
        name=body.name,
        created_by=current_user.id,
        client_name=body.client_name,
        submission_date=body.submission_date,
    )
    response = TenderResponse.model_validate(tender, from_attributes=True)
    await idempotency.finalize(201, response.model_dump(mode="json"))
    return response


@router.get("/{org_id}/tenders", response_model=list[TenderResponse])
async def list_tenders(
    org_id: UUID, current_user: User = Depends(require_permission("create_bid"))
) -> list[TenderResponse]:
    tenders = await tender_service.list_tenders(org_id)
    return [TenderResponse.model_validate(t, from_attributes=True) for t in tenders]


@router.post("/{org_id}/memberships", response_model=MembershipResponse, status_code=201)
async def add_membership(
    org_id: UUID,
    body: AddMembershipRequest,
    current_user: User = Depends(require_permission("manage_users_roles")),
) -> MembershipResponse:
    try:
        membership = await membership_service.add_membership(
            organization_id=org_id,
            email=body.email,
            role=body.role,
            actor_user_id=current_user.id,
        )
    except membership_service.UserNotFoundError as exc:
        raise UserNotFoundError() from exc
    except membership_service.AlreadyMemberError as exc:
        raise AlreadyMemberError() from exc

    return MembershipResponse.model_validate(membership, from_attributes=True)
