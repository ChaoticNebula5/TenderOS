from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProposalSectionResponse(BaseModel):
    id: UUID
    tender_id: UUID
    section_name: str
    content: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatchProposalSectionRequest(BaseModel):
    """G10's PATCH /proposal/sections/{section_id} - a human editing drafted
    content. content is the only editable field: this route changes prose,
    not grounding - re-running claim enforcement is what /proposal/generate
    is for, not an editor's PATCH.
    """

    content: str


class ClaimEvidenceTraceResponse(BaseModel):
    evidence_link_id: UUID
    source_kind: str
    excerpt: str | None
    verified: bool
    validity_status: str
    knowledge_item_id: UUID | None
    knowledge_item_title: str | None
    document_id: UUID | None
    document_filename: str | None


class ProposalClaimTraceResponse(BaseModel):
    claim_id: UUID
    claim_text: str
    claim_type: str
    is_factual: bool
    grounding_status: str
    section_id: UUID
    section_name: str
    requirement_ids: list[UUID] = Field(default_factory=list)
    employee_knowledge_item_ids: list[UUID] = Field(default_factory=list)
    evidence: list[ClaimEvidenceTraceResponse] = Field(default_factory=list)
