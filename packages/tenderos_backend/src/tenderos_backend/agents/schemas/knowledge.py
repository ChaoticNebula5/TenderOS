from pydantic import BaseModel, Field


class ExtractedCertificationMention(BaseModel):
    """A certification the resume's own prose claims the candidate holds -
    NOT the same thing as an authoritative certifications.xlsx row. This is
    what the person's resume says about themselves; it lands in
    employees.parsed_profile for a reviewer to see alongside (and possibly
    cross-check against) the organization's own certification records, not
    written into the certifications table directly.
    """

    name: str
    issuer: str | None = None
    expiry_date: str | None = None


class ExtractedProjectMention(BaseModel):
    """Same "claim, not authority" relationship to past_projects that
    ExtractedCertificationMention has to certifications - see there."""

    name: str
    client: str | None = None
    year: str | None = None


class ExtractedEmployeeProfile(BaseModel):
    name: str
    email: str | None = None
    current_title: str | None = None
    professional_location: str | None = None
    years_experience: float | None = None
    certification_mentions: list[ExtractedCertificationMention] = Field(default_factory=list)
    project_mentions: list[ExtractedProjectMention] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedPastProject(BaseModel):
    """Unlike ExtractedProjectMention above (a resume's unverified claim
    about one person), this IS the authoritative company record - a
    capability statement is the organization's own document describing its
    own delivered work, so this becomes a real past_projects row, not a
    parsed_profile note.
    """

    name: str
    client: str | None = None
    year: str | None = None
    description: str | None = None
    source_ref: str | None = None  # bracketed reference code if the source
    # text had one (e.g. "[PROJECT-01]") - preserved verbatim per the same
    # citation-preservation rule requirement_extraction.md already uses, not
    # invented here.
    source_chunk_ref: str  # which chunk label (e.g. "chunk_2") this project
    # was found in - same E1 "no fabricated IDs" reasoning as
    # ExtractedRequirement.source_chunk_ref in agents/schemas/requirements.py;
    # relationships.py/extract.py resolves the label to a real chunk UUID
    # after parsing, the model never sees or invents a real ID.


class ExtractedCapability(BaseModel):
    name: str
    description: str | None = None
    source_ref: str | None = None
    source_chunk_ref: str


class CapabilityStatementExtraction(BaseModel):
    past_projects: list[ExtractedPastProject] = Field(default_factory=list)
    capabilities: list[ExtractedCapability] = Field(default_factory=list)


class ExtractedRelationshipCandidate(BaseModel):
    """One proposed WORKED_ON (or, later, DEMONSTRATES) edge. target_ref is
    a short label ("project_1", "project_2", ...) the prompt assigns to each
    candidate project it shows the model - never a real knowledge_item_id,
    same "no fabricated IDs" reasoning as source_chunk_ref in
    agents/schemas/requirements.py. relationships.py resolves the label back
    to a real UUID after parsing.
    """

    target_ref: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class RelationshipExtractionOutput(BaseModel):
    relationships: list[ExtractedRelationshipCandidate] = Field(default_factory=list)
