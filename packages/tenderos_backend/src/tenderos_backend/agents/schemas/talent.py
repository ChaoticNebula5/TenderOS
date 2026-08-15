from pydantic import BaseModel, Field


class ExtractedRole(BaseModel):
    """Role extraction has no locked E-part contract the way E3 locks
    requirement extraction - Build Guide B8A just lists "role extraction"
    as a build item. Mirroring E3's ExtractedRequirement shape as the
    closest precedent: no role_requirement_id (server-assigned, the model
    has no way to know it) and source requirements referenced by label
    (source_requirement_refs), not real UUIDs - same E1 "no fabricated
    IDs" reasoning ExtractedRequirement.source_chunk_ref already
    established, applied here to requirement rows instead of chunks.
    extract_roles.py resolves each label back to a real requirement_id
    after parsing.
    """

    role_name: str
    min_experience_years: float | None = None
    required_skills: list[str] = Field(default_factory=list)
    required_certs: list[str] = Field(default_factory=list)
    source_requirement_refs: list[str] = Field(default_factory=list)


class RoleExtractionOutput(BaseModel):
    roles: list[ExtractedRole] = Field(default_factory=list)


class TalentGap(BaseModel):
    criterion: str
    status: str


class TalentStrength(BaseModel):
    criterion: str
    evidence_ref: str


class CandidateScoring(BaseModel):
    """E5's Talent Contract locks the *shape* of one candidate's scoring
    result (score, strengths[{criterion, evidence_ref}], gaps[{criterion,
    status}]) but its own JSON example shows literal "uuid" placeholders
    for both employee_knowledge_item_id and evidence_ref - the same shape
    E4's compliance-proposal example used before B7 deliberately deviated
    from it (ComplianceProposal.evidence_refs: list[str] of labels, not
    real UUIDs). Applying that same established deviation here: evidence_ref
    is a label (resolved to a real evidence_link by talent_service.py,
    exactly like compliance's evidence_refs), and employee_knowledge_item_id
    is omitted entirely rather than asked to be echoed back - this schema
    is the output of one call scoring one specific candidate against one
    specific role (score_candidate() is called once per role/candidate
    pair, mirroring propose_compliance's one-call-per-requirement shape),
    so which candidate is being scored is the call's own input, never
    something the model states or restates.
    """

    score: float = Field(ge=0.0, le=1.0)
    strengths: list[TalentStrength] = Field(default_factory=list)
    gaps: list[TalentGap] = Field(default_factory=list)
