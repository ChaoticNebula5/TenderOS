from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import operation_service, proposal_service, tender_service

from tenderos_api.dependencies import require_permission
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.operations import OperationResponse
from tenderos_api.schemas.proposal import (
    PatchProposalSectionRequest,
    ProposalClaimResponse,
    ProposalClaimTraceResponse,
    ProposalSectionResponse,
)

router = APIRouter(tags=["proposal"])

# One RBAC action for the whole G10 surface - Part H locks edit_proposal
# (BID_MANAGER/SOLUTION_ARCHITECT/PROPOSAL_WRITER/ADMIN) for exactly this,
# same "one action per G-part" shape G9's _RESOLVE constant uses.
_EDIT = "edit_proposal"


@router.get(
    "/tenders/{tender_id}/proposal/sections", response_model=list[ProposalSectionResponse]
)
async def list_proposal_sections(
    tender_id: UUID, current_user: User = Depends(require_permission(_EDIT))
) -> list[ProposalSectionResponse]:
    sections = await proposal_service.list_proposal_sections_for_tender(tender_id)
    return [ProposalSectionResponse.model_validate(s, from_attributes=True) for s in sections]


@router.post(
    "/tenders/{tender_id}/proposal/generate",
    response_model=OperationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_proposal(
    tender_id: UUID,
    current_user: User = Depends(require_permission(_EDIT)),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("proposal_generation", required=True)
    ),
) -> OperationResponse:
    """Phase 4 Stage 2: no longer drafts the proposal (up to 12 LLM calls)
    inside the request. Records a proposal_generation async operation and
    returns 202 with it, the same pattern as POST .../redteam/run; the
    worker runs draft_proposal_for_tender (jobs/operations.py), and the web
    app follows GET /operations/{operation_id} until it finishes, then
    re-reads GET .../proposal/sections. A second request while one is
    queued or running returns that same operation.

    Part I1 still requires an Idempotency-Key here. The key now records and
    replays this fast 202, so the request finishes well inside the key's
    60 s lease, which closes blocker 2's limit about generation outlasting
    it (KNOWN_LIMITATIONS.md). A replayed body shows the status at the time
    of the first request; the client follows the operation for the current
    one. proposal_drafting_node runs the same pipeline automatically once
    per workflow run.
    """
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return OperationResponse.model_validate(cached_body)

    tender = await tender_service.get_tender(tender_id)
    if tender is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
    requested = await operation_service.request_operation(
        tender.organization_id, tender_id, "proposal_generation", current_user.id
    )
    response = OperationResponse.from_operation(requested.operation)
    await idempotency.finalize(202, response.model_dump(mode="json"))
    return response


@router.get(
    "/proposal/sections/{section_id}/claims", response_model=list[ProposalClaimResponse]
)
async def list_proposal_claims(
    section_id: UUID, current_user: User = Depends(require_permission(_EDIT))
) -> list[ProposalClaimResponse]:
    """B13 addition - see proposal_service.list_claims_for_section's own
    docstring. Decided, not left open: stays on _EDIT even now that
    view_proposal_trace (§7 step 10) exists. Screen 13 (Proposal
    workspace), this route's only caller, is itself gated to edit_proposal
    end to end - a Talent/Commercial reviewer can't reach it to call this
    route regardless of this route's own RBAC. The trace route needed
    widening because it's reachable independently of that screen (a
    claim_id can be opened directly from a red-team finding or the
    readiness gate); this one has no equivalent standalone entry point, so
    there's no real gap to close by widening it too.
    """
    claims = await proposal_service.list_claims_for_section(section_id)
    return [ProposalClaimResponse.model_validate(c, from_attributes=True) for c in claims]


@router.patch("/proposal/sections/{section_id}", response_model=ProposalSectionResponse)
async def patch_proposal_section(
    section_id: UUID,
    body: PatchProposalSectionRequest,
    current_user: User = Depends(require_permission(_EDIT)),
) -> ProposalSectionResponse:
    try:
        updated = await proposal_service.update_section_content(
            section_id, body.content, current_user.id
        )
    except proposal_service.ProposalSectionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proposal section not found"
        ) from exc
    return ProposalSectionResponse.model_validate(updated, from_attributes=True)


@router.get("/proposal/claims/{claim_id}/trace", response_model=ProposalClaimTraceResponse)
async def trace_proposal_claim(
    claim_id: UUID, current_user: User = Depends(require_permission("view_proposal_trace"))
) -> ProposalClaimTraceResponse:
    try:
        trace = await proposal_service.get_proposal_trace(claim_id)
    except proposal_service.ProposalClaimNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Proposal claim not found"
        ) from exc
    return ProposalClaimTraceResponse.model_validate(trace, from_attributes=True)
