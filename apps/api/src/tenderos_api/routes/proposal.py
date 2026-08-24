from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import proposal_service

from tenderos_api.dependencies import require_permission
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
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


def _build_llm_client() -> LLMClient:
    # Same "function, not module-level singleton" shape every other route
    # file's own helper uses - kept here rather than imported from
    # orchestration.nodes.proposal, since A4.1 locks the dependency
    # direction as routes -> domain services, never routes -> orchestration.
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


@router.get(
    "/tenders/{tender_id}/proposal/sections", response_model=list[ProposalSectionResponse]
)
async def list_proposal_sections(
    tender_id: UUID, current_user: User = Depends(require_permission(_EDIT))
) -> list[ProposalSectionResponse]:
    sections = await proposal_service.list_proposal_sections_for_tender(tender_id)
    return [ProposalSectionResponse.model_validate(s, from_attributes=True) for s in sections]


@router.post(
    "/tenders/{tender_id}/proposal/generate", response_model=list[ProposalSectionResponse]
)
async def generate_proposal(
    tender_id: UUID,
    current_user: User = Depends(require_permission(_EDIT)),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("proposal_generation", required=True)
    ),
) -> list[ProposalSectionResponse]:
    """I1 explicitly names "proposal generation" as one of only six
    operations requiring Idempotency-Key (Part I1) - wired in from the
    start this time (B8A's select-candidate needed a second pass to catch
    this same requirement). This route triggers draft_proposal_for_tender
    directly, the same manual on-demand shape G9's /validate route gives
    commercial checks; proposal_drafting_node runs the same pipeline
    automatically once per workflow run.
    """
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return [ProposalSectionResponse.model_validate(s) for s in cached_body["sections"]]

    client = _build_llm_client()
    sections = await proposal_service.draft_proposal_for_tender(
        client, settings.reasoning_model, tender_id
    )
    response = [
        ProposalSectionResponse.model_validate(s, from_attributes=True) for s in sections
    ]
    await idempotency.finalize(
        200, {"sections": [r.model_dump(mode="json") for r in response]}
    )
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
