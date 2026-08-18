from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class RedteamFindingResponse(BaseModel):
    id: UUID
    tender_id: UUID
    target_type: str
    target_id: UUID | None
    finding_category: str
    severity: str
    finding_text: str
    rebuttal: str | None
    evidence_refs: list[Any]
    violated_condition: str
    resolved: bool
    resolution_note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ResolveFindingRequest(BaseModel):
    resolution_note: str | None = None
