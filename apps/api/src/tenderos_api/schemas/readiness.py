from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ReadinessReportResponse(BaseModel):
    id: UUID
    tender_id: UUID
    tender_state_version: int
    counts: dict[str, Any]
    blockers: list[dict[str, Any]]
    ready: bool
    generated_at: datetime

    model_config = {"from_attributes": True}
