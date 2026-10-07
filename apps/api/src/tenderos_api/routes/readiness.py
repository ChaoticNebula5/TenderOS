from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import readiness_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.readiness import ReadinessReportResponse

router = APIRouter(tags=["readiness"])

_VIEW = "view_readiness"
_GENERATE = "generate_readiness"


def _result_to_response(result: readiness_service.ReadinessResult) -> ReadinessReportResponse:
    return ReadinessReportResponse(
        id=result.report_id,
        tender_id=result.tender_id,
        tender_state_version=result.tender_state_version,
        counts=result.counts,
        blockers=result.blockers,
        ready=result.ready,
        generated_at=result.generated_at,
    )


@router.get("/tenders/{tender_id}/readiness", response_model=ReadinessReportResponse)
async def get_readiness(
    tender_id: UUID, current_user: User = Depends(require_permission(_VIEW))
) -> ReadinessReportResponse:
    result = await readiness_service.get_latest_readiness(tender_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No readiness report has been generated for this tender yet.",
        )
    return _result_to_response(result)


@router.post("/tenders/{tender_id}/readiness/generate", response_model=ReadinessReportResponse)
async def generate_readiness(
    tender_id: UUID, current_user: User = Depends(require_permission(_GENERATE))
) -> ReadinessReportResponse:
    # G11's manual trigger, same "on-demand re-run outside the automatic
    # graph timing" precedent as B10/B11's own manual trigger routes - a
    # human resolving a blocker via the normal G7/G8/G9/G11 actions can see
    # the updated readiness picture immediately, without waiting for the
    # workflow to reach readiness_generation_node again on its own. No
    # degraded_stages to pass here (this route has no TenderState, unlike
    # readiness_generation_node's own call) - a standalone regeneration
    # correctly can't know about an in-flight run's own failures.
    try:
        result = await readiness_service.evaluate_readiness(tender_id)
    except readiness_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    return _result_to_response(result)
