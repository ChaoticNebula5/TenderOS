from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.factory import build_llm_client
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import commercial_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.commercial import PricingDocumentResponse, PricingFindingResponse

router = APIRouter(tags=["commercial"])

# One RBAC action for every G9 route - Part H already locks
# resolve_commercial_finding (BID_MANAGER/COMMERCIAL_REVIEWER/APPROVER/
# ADMIN) for this whole surface, same "one action per G-part" shape G7 uses
# with approve_compliance.
_RESOLVE = "resolve_commercial_finding"


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape the orchestration
    # nodes' own helpers use - kept here rather than imported from
    # orchestration.nodes.commercial, since A4.1 locks the dependency
    # direction as routes -> domain services, never routes -> orchestration.
    # B17: delegates to agents/llm/factory.py::build_llm_client, the one
    # real gemini-vs-fake decision (was its own independent GeminiClient
    # construction - see KNOWN_LIMITATIONS.md's "settings.llm_provider is
    # dead configuration" entry, which named this exact call site as one
    # of the 12 that made the original provider switch inert).
    return build_llm_client()


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
    response_model=list[PricingFindingResponse],
)
async def validate_pricing_document(
    pricing_document_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> list[PricingFindingResponse]:
    """G9's manual re-validation trigger - F3's validate_pricing_document
    internal tool, exposed over HTTP. commercial_analysis_node already runs
    the full check+explain+persist pipeline automatically during the
    workflow; this route lets a reviewer re-run it on demand (e.g. after
    noticing something) without waiting for a new workflow run. Re-runs the
    check across the WHOLE tender, not just this one pricing_document,
    since cross_doc_mismatch is inherently a cross-document question - the
    idempotent fingerprint lookups in commercial_service.py mean this never
    duplicates findings that already exist.
    """
    pricing_document = await commercial_service.get_pricing_document(pricing_document_id)
    if pricing_document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pricing document not found"
        )
    client = _build_llm_client()
    findings = await commercial_service.evaluate_commercial_for_tender(
        client, settings.reasoning_model, pricing_document.tender_id
    )
    return [PricingFindingResponse.model_validate(f, from_attributes=True) for f in findings]


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
