from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import commercial_service, operation_service, tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.commercial import PricingDocumentResponse, PricingFindingResponse
from tenderos_api.schemas.operations import OperationResponse

router = APIRouter(tags=["commercial"])

# One RBAC action for every G9 route - Part H already locks
# resolve_commercial_finding (BID_MANAGER/COMMERCIAL_REVIEWER/APPROVER/
# ADMIN) for this whole surface, same "one action per G-part" shape G7 uses
# with approve_compliance.
_RESOLVE = "resolve_commercial_finding"


@router.get(
    "/tenders/{tender_id}/pricing-documents", response_model=list[PricingDocumentResponse]
)
async def list_pricing_documents(
    tender_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> list[PricingDocumentResponse]:
    documents = await commercial_service.list_pricing_documents_for_tender(tender_id)
    return [PricingDocumentResponse.model_validate(d, from_attributes=True) for d in documents]


@router.post(
    "/pricing-documents/{pricing_document_id}/validate",
    response_model=OperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def validate_pricing_document(
    pricing_document_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> OperationResponse:
    """G9's manual re-validation trigger - F3's validate_pricing_document
    internal tool, exposed over HTTP. commercial_analysis_node already runs
    the full check+explain+persist pipeline automatically during the
    workflow; this route lets a reviewer re-run it on demand. It re-runs
    the check across the WHOLE tender, not just this one pricing_document,
    since cross_doc_mismatch is inherently a cross-document question.

    Phase 4 Stage 2: no longer runs the pipeline (and its explanation LLM
    calls) inside the request. Records a commercial_validation async
    operation for the document's tender and returns 202 with it, the same
    pattern as POST .../redteam/run; the worker runs
    evaluate_commercial_for_tender (jobs/operations.py), which looks each
    finding up by fingerprint and explains only the ones not stored yet.
    One operation per tender: a request from any document of the tender
    while one is queued or running returns that same operation. The web
    app follows it, then re-reads GET .../pricing-findings.
    """
    pricing_document = await commercial_service.get_pricing_document(pricing_document_id)
    if pricing_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pricing document not found"
        )
    tender = await tender_service.get_tender(pricing_document.tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    requested = await operation_service.request_operation(
        tender.organization_id, tender.id, "commercial_validation", current_user.id
    )
    return OperationResponse.from_operation(requested.operation)


@router.get("/tenders/{tender_id}/pricing-findings", response_model=list[PricingFindingResponse])
async def list_pricing_findings(
    tender_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> list[PricingFindingResponse]:
    findings = await commercial_service.list_pricing_findings_for_tender(tender_id)
    return [PricingFindingResponse.model_validate(f, from_attributes=True) for f in findings]


@router.post(
    "/pricing-findings/{pricing_finding_id}/resolve", response_model=PricingFindingResponse
)
async def resolve_pricing_finding(
    pricing_finding_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> PricingFindingResponse:
    try:
        updated = await commercial_service.resolve_pricing_finding(
            pricing_finding_id, current_user.id
        )
    except commercial_service.PricingFindingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pricing finding not found"
        ) from exc
    return PricingFindingResponse.model_validate(updated, from_attributes=True)
