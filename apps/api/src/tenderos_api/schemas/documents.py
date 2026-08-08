from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: UUID
    tender_id: UUID | None
    organization_id: UUID | None
    side: str
    doc_type: str
    filename: str
    sha256: str
    mime_type: str | None
    file_size_bytes: int | None
    page_count: int | None
    parse_status: str
    version: int
    created_at: datetime

    # storage_key is deliberately absent: it is an internal object-storage
    # path, and A5.1 keeps infrastructure detail out of API responses.
    # Clients address documents by id and never touch the bucket directly.
    model_config = {"from_attributes": True}
