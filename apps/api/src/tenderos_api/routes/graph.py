from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import graph_service

from tenderos_api.dependencies import require_permission
from tenderos_api.errors import GraphStaleAPIError
from tenderos_api.idempotency import IdempotencyContext, get_idempotency_context
from tenderos_api.schemas.graph import (
    GraphContradictionRow,
    GraphImpactRequest,
    GraphImpactRow,
    GraphProjectResponse,
    GraphStatusResponse,
    GraphTraceResponse,
)

router = APIRouter(tags=["graph"])

_VIEW = "view_graph"
_TRIGGER = "trigger_graph_projection"


def _reraise_as_stale(exc: graph_service.GraphStaleError) -> None:
    raise GraphStaleAPIError(exc.current_state_version, exc.projected_state_version) from exc


@router.post("/tenders/{tender_id}/graph/project", response_model=GraphProjectResponse)
async def trigger_graph_projection(
    tender_id: UUID,
    current_user: User = Depends(require_permission(_TRIGGER)),
    idempotency: IdempotencyContext = Depends(
        get_idempotency_context("graph_projection_trigger", required=True)
    ),
) -> GraphProjectResponse:
    """I1 explicitly names "graph projection trigger" as one of the six
    operations requiring Idempotency-Key - wired in from the start, same
    discipline B9's /proposal/generate already established. This route is
    the manual out-of-band trigger; project_graph_node (B10's orchestration
    node) runs the same underlying graph_service.project_tender
    automatically once per workflow run, same "manual route + automatic
    node call the same service function" shape G9's /validate route and
    commercial_analysis_node already share.
    """
    if idempotency.replay_response is not None:
        _, cached_body = idempotency.replay_response
        return GraphProjectResponse.model_validate(cached_body)

    try:
        run = await graph_service.project_tender(tender_id)
    except graph_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc

    response = GraphProjectResponse.model_validate(run, from_attributes=True)
    await idempotency.finalize(200, response.model_dump(mode="json"))
    return response


@router.get("/tenders/{tender_id}/graph/status", response_model=GraphStatusResponse)
async def get_graph_status(
    tender_id: UUID, current_user: User = Depends(require_permission(_VIEW))
) -> GraphStatusResponse:
    try:
        status_dict = await graph_service.get_graph_status(tender_id)
    except graph_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    return GraphStatusResponse.model_validate(status_dict)


@router.post("/tenders/{tender_id}/graph/impact", response_model=list[GraphImpactRow])
async def preview_graph_impact(
    tender_id: UUID,
    body: GraphImpactRequest,
    current_user: User = Depends(require_permission(_VIEW)),
) -> list[GraphImpactRow]:
    try:
        return await graph_service.preview_impact(tender_id, body.knowledge_item_id)
    except graph_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    except graph_service.GraphStaleError as exc:
        _reraise_as_stale(exc)
        raise  # unreachable - _reraise_as_stale always raises; satisfies type checkers


@router.get(
    "/tenders/{tender_id}/graph/contradictions", response_model=list[GraphContradictionRow]
)
async def get_graph_contradictions(
    tender_id: UUID, current_user: User = Depends(require_permission(_VIEW))
) -> list[GraphContradictionRow]:
    try:
        return await graph_service.find_contradictions(tender_id)
    except graph_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    except graph_service.GraphStaleError as exc:
        _reraise_as_stale(exc)
        raise


@router.get("/tenders/{tender_id}/graph/trace", response_model=GraphTraceResponse)
async def get_graph_trace(
    tender_id: UUID,
    node_label: str,
    node_id: str,
    current_user: User = Depends(require_permission(_VIEW)),
) -> GraphTraceResponse:
    try:
        return await graph_service.get_graph_trace(tender_id, node_label, node_id)
    except graph_service.TenderNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found"
        ) from exc
    except graph_service.GraphStaleError as exc:
        _reraise_as_stale(exc)
        raise
    except ValueError as exc:
        # graph_service.get_graph_trace's own allowlist check on node_label
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
