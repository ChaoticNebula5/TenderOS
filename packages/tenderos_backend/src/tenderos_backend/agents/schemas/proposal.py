from typing import Literal

from pydantic import BaseModel, Field

# Matches proposal_claims.claim_type's CHECK constraint (Part C) verbatim.
ClaimType = Literal["capability", "experience", "team", "compliance", "commercial", "other"]


class ProposalClaimDraft(BaseModel):
    """E7's per-claim shape, with the same label deviation B7/B8A already
    established for evidence_refs (ComplianceProposal, CandidateScoring):
    E7's own JSON example shows requirement_ids/evidence_ids/employee_ids as
    literal "uuid" placeholders, but the model is never handed real UUIDs to
    echo back - only the labels draft_section.py assigns to the grounded
    context it builds for this one call. proposal_service.py resolves each
    label back to a real row after parsing, exactly like
    materialize_evidence does for compliance.

    grounding_status is deliberately absent here - E7's contract does not
    ask the model to self-certify groundedness, and doing so would let it
    mark its own claim "grounded" while enforce_claim_grounding is the only
    thing actually allowed to decide that (this schema's is_factual is
    input to that decision, not a substitute for it).
    """

    claim_text: str
    claim_type: ClaimType
    is_factual: bool = True
    requirement_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    employee_refs: list[str] = Field(default_factory=list)


class ProposalSectionDraft(BaseModel):
    """One call drafts one section (draft_section.py is called once per
    section template, mirroring propose_compliance's one-call-per-
    requirement shape) - section_name is the call's own input, never
    something the model states or restates, same omission CandidateScoring
    already established for employee_knowledge_item_id.
    """

    content: str
    claims: list[ProposalClaimDraft] = Field(default_factory=list)
