from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.tenders import TenderResponse, TenderUpdateRequest

router = APIRouter(prefix="/tenders", tags=["tenders"])


@router.get("/{tender_id}", response_model=TenderResponse)
async def get_tender(
    tender_id: UUID, current_user: User = Depends(require_permission("create_bid"))
) -> TenderResponse:
    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return TenderResponse.model_validate(tender, from_attributes=True)


@router.patch("/{tender_id}", response_model=TenderResponse)
async def update_tender(
    tender_id: UUID,
    body: TenderUpdateRequest,
    current_user: User = Depends(require_permission("create_bid")),
) -> TenderResponse:
    tender = await tender_service.update_tender(
        tender_id,
        name=body.name,
        client_name=body.client_name,
        submission_date=body.submission_date,
        status=body.status,
    )
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    return TenderResponse.model_validate(tender, from_attributes=True)
