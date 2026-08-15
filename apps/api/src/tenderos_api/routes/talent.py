from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import talent_service

from tenderos_api.dependencies import require_permission
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.talent import (
    RoleMatchResponse,
    RoleRequirementResponse,
    SelectCandidateRequest,
)

router = APIRouter(tags=["talent"])


@router.get("/tenders/{tender_id}/roles", response_model=list[RoleRequirementResponse])
async def list_roles(
    tender_id: UUID,
    current_user: User = Depends(require_permission("select_resource")),
) -> list[RoleRequirementResponse]:
    roles = await talent_service.list_role_requirements_for_tender(tender_id)
    return [RoleRequirementResponse.model_validate(r, from_attributes=True) for r in roles]


@router.get("/roles/{role_id}/matches", response_model=list[RoleMatchResponse])
async def list_matches(
    role_id: UUID,
    current_user: User = Depends(require_permission("select_resource")),
) -> list[RoleMatchResponse]:
    matches = await talent_service.list_role_matches_for_role(role_id)
    return [RoleMatchResponse.model_validate(m, from_attributes=True) for m in matches]


@router.post("/roles/{role_id}/select-candidate", response_model=RoleMatchResponse)
async def select_candidate(
    role_id: UUID,
    body: SelectCandidateRequest,
    current_user: User = Depends(require_permission("select_resource")),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("resource_selection", required=True)
    ),
) -> RoleMatchResponse:
    """I1 explicitly names "resource selection" as one of only six
    operations requiring Idempotency-Key (Part I1) - same required=True
    shape workflow.py's create_workflow_run and gates.py's decide_gate
    already use, so a retried select/deselect click can't double-audit or
    double-bump tenders.state_version.
    """
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return RoleMatchResponse.model_validate(cached_body)

    try:
        updated = await talent_service.set_candidate_selection(
            role_id, body.employee_knowledge_item_id, current_user.id, selected=body.selected
        )
    except talent_service.RoleMatchNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role match not found for this candidate"
        ) from exc
    response = RoleMatchResponse.model_validate(updated, from_attributes=True)
    await idempotency.finalize(200, response.model_dump(mode="json"))
    return response
