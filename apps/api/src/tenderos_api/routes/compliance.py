from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import compliance_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.compliance import (
    ComplianceResponse,
    ComplianceTraceResponse,
    PatchComplianceRequest,
)

router = APIRouter(tags=["compliance"])


@router.get("/tenders/{tender_id}/compliance", response_model=list[ComplianceResponse])
async def list_compliance(
    tender_id: UUID,
    current_user: User = Depends(require_permission("approve_compliance")),
) -> list[ComplianceResponse]:
    entries = await compliance_service.list_compliance_entries_for_tender(tender_id)
    return [ComplianceResponse.model_validate(e, from_attributes=True) for e in entries]


@router.get("/compliance/{compliance_id}", response_model=ComplianceResponse)
async def get_compliance(
    compliance_id: UUID,
    current_user: User = Depends(require_permission("approve_compliance")),
) -> ComplianceResponse:
    entry = await compliance_service.get_compliance_entry(compliance_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance entry not found")
    return ComplianceResponse.model_validate(entry, from_attributes=True)


@router.patch("/compliance/{compliance_id}", response_model=ComplianceResponse)
async def patch_compliance(
    compliance_id: UUID,
    body: PatchComplianceRequest,
    current_user: User = Depends(require_permission("approve_compliance")),
) -> ComplianceResponse:
    """A human directly overriding the proposed status and/or reasoning -
    always audited, always sets human_approved=True (compliance_service.
    override_compliance_entry). Unlike requirements' PATCH (which supports a
    bare confirm with no field edits), this route expects at least one real
    edit; PRD 8.4's evidence-sufficiency invariant is NOT re-checked against
    an override - a human overriding the system's own proposal is exactly
    the "explicit human-approved exception" PRD 8.4 itself carves out.
    """
    try:
        updated = await compliance_service.override_compliance_entry(
            compliance_id,
            current_user.id,
            status=body.status,
            reasoning_summary=body.reasoning_summary,
        )
    except compliance_service.ComplianceEntryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Compliance entry not found"
        ) from exc
    return ComplianceResponse.model_validate(updated, from_attributes=True)


@router.post("/compliance/{compliance_id}/approve", response_model=ComplianceResponse)
async def approve_compliance(
    compliance_id: UUID,
    current_user: User = Depends(require_permission("approve_compliance")),
) -> ComplianceResponse:
    try:
        updated = await compliance_service.approve_compliance_entry(compliance_id, current_user.id)
    except compliance_service.ComplianceEntryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Compliance entry not found"
        ) from exc
    return ComplianceResponse.model_validate(updated, from_attributes=True)


@router.get("/compliance/{compliance_id}/trace", response_model=ComplianceTraceResponse)
async def trace_compliance(
    compliance_id: UUID,
    current_user: User = Depends(require_permission("approve_compliance")),
) -> ComplianceTraceResponse:
    try:
        trace = await compliance_service.get_compliance_trace(compliance_id)
    except compliance_service.ComplianceEntryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Compliance entry not found"
        ) from exc
    return ComplianceTraceResponse.model_validate(trace, from_attributes=True)
