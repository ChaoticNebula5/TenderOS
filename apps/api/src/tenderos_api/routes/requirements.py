from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import requirement_service, tender_service

from tenderos_api.dependencies import require_merge_permission, require_permission
from tenderos_api.schemas.requirements import (
    MergeRequirementsRequest,
    PatchRequirementRequest,
    PatchSubmissionItemRequest,
    RequirementResponse,
    SplitRequirementRequest,
    SubmissionItemResponse,
)

router = APIRouter(tags=["requirements"])


@router.get("/tenders/{tender_id}/requirements", response_model=list[RequirementResponse])
async def list_requirements(
    tender_id: UUID,
    current_user: User = Depends(require_permission("review_requirements")),
) -> list[RequirementResponse]:
    requirements = await requirement_service.list_requirements_for_tender(tender_id)
    return [RequirementResponse.model_validate(r, from_attributes=True) for r in requirements]


@router.get("/requirements/{requirement_id}", response_model=RequirementResponse)
async def get_requirement(
    requirement_id: UUID,
    current_user: User = Depends(require_permission("review_requirements")),
) -> RequirementResponse:
    requirement = await requirement_service.get_requirement(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    return RequirementResponse.model_validate(requirement, from_attributes=True)


@router.patch("/requirements/{requirement_id}", response_model=RequirementResponse)
async def patch_requirement(
    requirement_id: UUID,
    body: PatchRequirementRequest,
    current_user: User = Depends(require_permission("review_requirements")),
) -> RequirementResponse:
    requirement = await requirement_service.get_requirement(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    tender = await tender_service.get_tender(requirement.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    updated = await requirement_service.patch_requirement(
        requirement_id,
        tender.organization_id,
        current_user.id,
        requirement_text=body.requirement_text,
        category=body.category,
        mandatory=body.mandatory,
        action=body.action,
        notes=body.notes,
    )
    return RequirementResponse.model_validate(updated, from_attributes=True)


@router.post(
    "/requirements/{requirement_id}/verify",
    response_model=RequirementResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def verify_requirement(
    requirement_id: UUID,
    current_user: User = Depends(require_permission("review_requirements")),
) -> RequirementResponse:
    """202: the real verification call happens later, on the worker (line
    538 forbids it inside this request) - the response reflects the
    requirement's state at request time, not a post-verification one that
    doesn't exist yet. Re-poll GET /requirements/{id} for the result."""
    requirement = await requirement_service.get_requirement(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")

    await requirement_service.request_verification(requirement_id)
    return RequirementResponse.model_validate(requirement, from_attributes=True)


@router.post("/requirements/merge", response_model=RequirementResponse)
async def merge_requirements(
    body: MergeRequirementsRequest,
    current_user: User = Depends(require_merge_permission),
) -> RequirementResponse:
    first = await requirement_service.get_requirement(body.requirement_ids[0])
    if first is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    tender = await tender_service.get_tender(first.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    try:
        merged = await requirement_service.merge_requirements(
            body.requirement_ids,
            body.merged_text,
            body.category,
            body.mandatory,
            tender.organization_id,
            current_user.id,
        )
    except requirement_service.CrossTenderOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All requirement_ids must belong to the same tender",
        ) from exc
    except requirement_service.RequirementNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found"
        ) from exc

    return RequirementResponse.model_validate(merged, from_attributes=True)


@router.post("/requirements/{requirement_id}/split", response_model=list[RequirementResponse])
async def split_requirement(
    requirement_id: UUID,
    body: SplitRequirementRequest,
    current_user: User = Depends(require_permission("review_requirements")),
) -> list[RequirementResponse]:
    requirement = await requirement_service.get_requirement(requirement_id)
    if requirement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Requirement not found")
    tender = await tender_service.get_tender(requirement.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    new_items = [item.model_dump(exclude_none=True) for item in body.new_items]
    created = await requirement_service.split_requirement(
        requirement_id, new_items, tender.organization_id, current_user.id
    )
    return [RequirementResponse.model_validate(r, from_attributes=True) for r in created]


# review_requirements, not a dedicated action: Part H's RBAC matrix never
# named submission items at all (the same gap B13 already found for the
# whole 17-screen list) - reused deliberately, not inherited by default.
# A reviewer editing/confirming a requirement and a reviewer marking a
# submission item received are the same trust tier: both are "a human
# with review authority over this tender's extracted checklist confirms
# what's actually true," not a different kind of decision. If submission-
# item handling ever needs its own escalation path (e.g. an approver-only
# override once items feed into a formal sign-off), that's the point to
# split this into a dedicated RBAC row - not assumed necessary now.
@router.get(
    "/tenders/{tender_id}/submission-items", response_model=list[SubmissionItemResponse]
)
async def list_submission_items(
    tender_id: UUID,
    current_user: User = Depends(require_permission("review_requirements")),
) -> list[SubmissionItemResponse]:
    items = await requirement_service.list_submission_items_for_tender(tender_id)
    return [SubmissionItemResponse.model_validate(i, from_attributes=True) for i in items]


@router.patch("/submission-items/{submission_item_id}", response_model=SubmissionItemResponse)
async def patch_submission_item(
    submission_item_id: UUID,
    body: PatchSubmissionItemRequest,
    current_user: User = Depends(require_permission("review_requirements")),
) -> SubmissionItemResponse:
    # Path param is submission_item_id, not item_id - dependencies.py's
    # _resolve_organization_id already has an item_id branch wired to
    # knowledge_import_items (B6), an unrelated resource; item_id here
    # would have silently resolved organization from the wrong table.
    item = await requirement_service.get_submission_item(submission_item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission item not found")
    tender = await tender_service.get_tender(item.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")

    updated = await requirement_service.patch_submission_item(
        submission_item_id,
        tender.organization_id,
        current_user.id,
        status=body.status,
        matched_document_id=body.matched_document_id,
    )
    return SubmissionItemResponse.model_validate(updated, from_attributes=True)
