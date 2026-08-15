from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PricingDocumentResponse(BaseModel):
    id: UUID
    tender_id: UUID
    document_id: UUID
    sheet_name: str
    currency: str | None

    model_config = {"from_attributes": True}


class PricingFindingResponse(BaseModel):
    id: UUID
    tender_id: UUID
    finding_type: str
    item_a_ref: dict | None
    item_b_ref: dict | None
    delta: float | None
    severity: str
    note: str
    resolved: bool
    created_at: datetime

    model_config = {"from_attributes": True}
