from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel


class TenderCreateRequest(BaseModel):
    name: str
    client_name: str | None = None
    submission_date: date | None = None


class TenderUpdateRequest(BaseModel):
    name: str | None = None
    client_name: str | None = None
    submission_date: date | None = None
    status: str | None = None


class TenderResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    client_name: str | None
    submission_date: date | None
    status: str
    state_version: int
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
