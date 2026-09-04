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


class ProposalClaimResponse(BaseModel):
    """B13 addition: GET /proposal/sections/{section_id}/claims. Screen 13's
    only way to discover which claim ids exist in a section - see
    proposal_service.list_claims_for_section's own docstring for why this
    didn't already exist. List-item shape only (no evidence/trace fields);
    ProposalClaimTraceResponse below is what a click on one of these opens.
    """

    id: UUID
    section_id: UUID
    claim_text: str
    claim_type: str
    is_factual: bool
    claim_kind: str
    grounding_status: str

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
    claim_kind: str
    grounding_status: str
    section_id: UUID
    section_name: str
    requirement_ids: list[UUID] = Field(default_factory=list)
    employee_knowledge_item_ids: list[UUID] = Field(default_factory=list)
    evidence: list[ClaimEvidenceTraceResponse] = Field(default_factory=list)
    # Re-derived at view time from the claim's own section - what's
    # CURRENTLY in scope, not a record of what was available when drafting
    # ran, and never overlapping with `evidence` above. See
    # domain/services/proposal_service.py::ProposalClaimTrace for the full
    # rationale.
    currently_in_scope_evidence: list[ClaimEvidenceTraceResponse] = Field(default_factory=list)
    section_is_data_gated: bool = True
