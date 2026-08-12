from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class KnowledgeItemResponse(BaseModel):
    id: UUID
    organization_id: UUID
    item_type: str
    title: str
    status: str
    valid_from: date | None
    valid_until: date | None
    source_document_id: UUID | None
    source_chunk_id: UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmployeeResponse(BaseModel):
    knowledge_item_id: UUID
    organization_id: UUID
    employee_code: str | None
    name: str
    email: str | None
    current_title: str | None
    years_experience: float | None
    professional_location: str | None
    availability_status: str
    availability_as_of: date | None
    eligible_for_bid: bool
    profile_review_status: str
    profile_last_verified_at: datetime | None
    profile_verified_by: UUID | None
    resume_document_id: UUID | None

    # parsed_profile is deliberately absent: it carries the raw LLM
    # extraction (certification_mentions/project_mentions and everything
    # else the model produced), which is review-queue detail, not the
    # steady-state profile shape most callers of this response need.
    model_config = {"from_attributes": True}


class ImportBatchResponse(BaseModel):
    id: UUID
    organization_id: UUID
    import_type: str
    source_document_id: UUID | None
    status: str
    total_items: int
    completed_items: int
    failed_items: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ImportItemResponse(BaseModel):
    id: UUID
    batch_id: UUID
    source_ref: str
    status: str
    target_knowledge_item_id: UUID | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    # extracted_payload is deliberately absent from the list/summary shape
    # used elsewhere - it is exposed only where a reviewer actually needs
    # to see what was parsed (the item detail view), not on every list row.
    model_config = {"from_attributes": True}


class ImportItemDetailResponse(ImportItemResponse):
    extracted_payload: dict


class KnowledgeSelectionResponse(BaseModel):
    id: UUID
    tender_id: UUID
    knowledge_item_id: UUID
    selected: bool
    selected_at: datetime | None
    selected_by: UUID | None
    deselected_at: datetime | None
    deselected_by: UUID | None
    change_reason: str | None

    model_config = {"from_attributes": True}


class PatchKnowledgeItemRequest(BaseModel):
    title: str | None = None
    status: str | None = Field(default=None, pattern="^(active|expired|archived|unverified)$")


class PatchEmployeeProfileRequest(BaseModel):
    """G6's PATCH /employees/{id}/profile - correction, not approval. See
    knowledge_service.patch_employee_profile for why eligible_for_bid is
    deliberately not a field here."""

    name: str | None = None
    email: str | None = None
    current_title: str | None = None
    professional_location: str | None = None
    years_experience: float | None = None
    availability_status: str | None = Field(
        default=None, pattern="^(available|partially_available|unavailable|unknown)$"
    )
    availability_as_of: date | None = None


class VerifyEmployeeRequest(BaseModel):
    """G6's POST /employees/{id}/verify - the reviewer must explicitly
    decide bid-eligibility as part of approving, not as a separate step."""

    eligible_for_bid: bool


class RejectImportItemRequest(BaseModel):
    reason: str | None = None


class SetKnowledgeSelectionRequest(BaseModel):
    selected: bool
    change_reason: str | None = None
