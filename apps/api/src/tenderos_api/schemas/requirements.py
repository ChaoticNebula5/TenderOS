from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field
from tenderos_backend.agents.schemas.requirements import RequirementCategory


class RequirementResponse(BaseModel):
    id: UUID
    tender_id: UUID
    display_code: str
    requirement_text: str
    category: str
    mandatory: bool
    source_chunk_id: UUID
    confidence: float | None
    verified: bool
    human_reviewed: bool
    rejected_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatchRequirementRequest(BaseModel):
    """G5's PATCH /requirements/{id} - covers confirm/edit/reject (PRD 10.3)
    in one body shape. `action=None` with no field edits is a bare confirm;
    `action="reject"` ignores any field edits and soft-deletes instead."""

    requirement_text: str | None = None
    category: RequirementCategory | None = None
    mandatory: bool | None = None
    action: str | None = Field(default=None, pattern="^(confirm|reject)$")
    notes: str | None = None


class MergeRequirementsRequest(BaseModel):
    requirement_ids: list[UUID] = Field(min_length=2)
    merged_text: str
    category: RequirementCategory
    mandatory: bool


class SplitItemRequest(BaseModel):
    requirement_text: str
    category: RequirementCategory | None = None
    mandatory: bool | None = None


class SplitRequirementRequest(BaseModel):
    new_items: list[SplitItemRequest] = Field(min_length=2)


class SubmissionItemResponse(BaseModel):
    id: UUID
    tender_id: UUID
    name: str
    mandatory: bool
    status: str
    source_chunk_id: UUID | None
    matched_document_id: UUID | None

    model_config = {"from_attributes": True}


class PatchSubmissionItemRequest(BaseModel):
    """The write path readiness_service.py's missing_mandatory_submission_item
    blocker (and redteam_checks.py's identically-named finding) have both
    been waiting on since B5 - see requirement_service.patch_submission_item.
    """

    status: Literal["missing", "present", "needs_review", "not_applicable"]
    matched_document_id: UUID | None = None
