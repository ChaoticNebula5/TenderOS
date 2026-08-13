from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
from tenderos_backend.agents.schemas.compliance import ComplianceStatus


class ComplianceResponse(BaseModel):
    id: UUID
    requirement_id: UUID
    status: str
    reasoning_summary: str | None
    confidence: float | None
    human_approved: bool
    last_evaluated_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PatchComplianceRequest(BaseModel):
    """G7's PATCH /compliance/{compliance_id} - a human override. Both
    fields optional (a reviewer may edit just the status, just the
    reasoning, or both) but at least one edit is expected; the route itself
    still always sets human_approved=True and always audits, even if only
    one field changed.
    """

    status: ComplianceStatus | None = None
    reasoning_summary: str | None = None


class EvidenceTraceResponse(BaseModel):
    evidence_link_id: UUID
    source_kind: str
    excerpt: str | None
    verified: bool
    validity_status: str
    knowledge_item_id: UUID | None
    knowledge_item_title: str | None
    document_id: UUID | None
    document_filename: str | None
    page_num: int | None
    heading_path: list[str] | None


class ComplianceTraceResponse(BaseModel):
    requirement_id: UUID
    requirement_text: str
    compliance_entry_id: UUID
    status: str
    reasoning_summary: str | None
    human_approved: bool
    evidence: list[EvidenceTraceResponse] = Field(default_factory=list)
