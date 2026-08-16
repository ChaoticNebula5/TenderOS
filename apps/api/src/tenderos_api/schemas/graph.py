from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class GraphProjectResponse(BaseModel):
    id: UUID
    tender_id: UUID
    source_state_version: int
    status: str
    node_count: int | None
    relationship_count: int | None

    model_config = {"from_attributes": True}


class GraphStatusResponse(BaseModel):
    fresh: bool
    current_state_version: int
    projected_state_version: int | None
    last_projected_at: datetime | None


class GraphImpactRequest(BaseModel):
    """POST .../graph/impact's only input. Deliberately has NO hop-count
    field - the traversal depth always comes from settings.graph_max_hops
    (D4's server-side config), never from the caller. See graph_service.
    preview_impact and the B10 plan's hop-count boundary note.
    """

    knowledge_item_id: UUID


# Impact rows, contradiction rows, and trace results are genuinely
# heterogeneous by nature (D5's query returns whatever labels/properties the
# affected node happens to have; D6's 4 contradiction categories each carry
# different fields - an expired-cert row has cert/evidence/compliance/
# requirement, a deselected-fact row has fact/evidence/via/target). Forcing
# these into fixed-field Pydantic models would either mismatch the actual
# Neo4j record shape or silently drop fields. Routes return these as plain
# dicts/lists (typed loosely here for the OpenAPI schema, not reshaped) -
# same "raw graph data, exploratory by design" posture as G13's own
# "metadata only" progress events.
GraphImpactRow = dict[str, Any]
GraphContradictionRow = dict[str, Any]
GraphTraceResponse = dict[str, Any]
