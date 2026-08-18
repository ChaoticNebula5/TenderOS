from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis as AsyncRedis
from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import redteam_service

from tenderos_api.dependencies import require_permission
from tenderos_api.schemas.redteam import RedteamFindingResponse, ResolveFindingRequest

router = APIRouter(tags=["redteam"])

# Part H's matrix already has "Resolve red-team finding" as a row (7 roles,
# scaffolded since B3 - see rbac.py's own comment) - both read/resolve
# routes below use it, matching G12's own "narrower write action, same
# view action" pattern would suggest if this block needed two different
# actions, which it doesn't: reading findings and resolving them are the
# same reviewer activity Part H already named as one action.
_RESOLVE = "resolve_redteam_finding"

# G11 also lists two `readiness` routes (GET/POST .../readiness) - not
# built here. Build Guide scopes real readiness generation ("readiness
# report...", "final-readiness gate") to B12's own build list; B11's build
# list never mentions readiness at all.

# NOT in G11's locked 3-route list (2 red-team + the readiness pair scoped
# to B12) - an interpretation, flagged the same way B10 flagged its RBAC
# additions. Graph projection (B10) has both an automatic node call AND a
# manual POST /graph/project trigger for the exact same reason this route
# exists: a human may want to re-run red-team review on demand (e.g. after
# resolving findings via the normal G7/G8/G9 actions, before the next
# automatic revision would otherwise fire), and without this route there
# is no way to observe red_team's own detection behavior independent of
# the full graph's automatic timing. trigger_graph_projection is the
# closest existing precedent for both the RBAC choice and the route shape.


def _build_llm_client() -> LLMClient:
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )


@router.post("/tenders/{tender_id}/redteam/run", response_model=list[RedteamFindingResponse])
async def trigger_red_team(
    tender_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> list[RedteamFindingResponse]:
    client = _build_llm_client()
    await redteam_service.run_red_team(client, settings.reasoning_model, tender_id)
    findings = await redteam_service.list_findings_for_tender(tender_id)
    return [RedteamFindingResponse.model_validate(f, from_attributes=True) for f in findings]


@router.get("/tenders/{tender_id}/redteam-findings", response_model=list[RedteamFindingResponse])
async def list_redteam_findings(
    tender_id: UUID, current_user: User = Depends(require_permission(_RESOLVE))
) -> list[RedteamFindingResponse]:
    findings = await redteam_service.list_findings_for_tender(tender_id)
    return [RedteamFindingResponse.model_validate(f, from_attributes=True) for f in findings]


@router.post("/redteam-findings/{finding_id}/resolve", response_model=RedteamFindingResponse)
async def resolve_redteam_finding(
    finding_id: UUID,
    body: ResolveFindingRequest,
    current_user: User = Depends(require_permission(_RESOLVE)),
) -> RedteamFindingResponse:
    try:
        finding = await redteam_service.resolve_finding(
            finding_id, current_user.id, body.resolution_note
        )
    except redteam_service.FindingNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found"
        ) from exc
    return RedteamFindingResponse.model_validate(finding, from_attributes=True)
