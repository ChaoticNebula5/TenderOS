from typing import Literal

from pydantic import BaseModel, Field

RequirementCategory = Literal[
    "technical",
    "staffing",
    "experience",
    "commercial",
    "eligibility",
    "submission",
    "legal",
    "security",
    "other",
]


class ExtractedRequirement(BaseModel):
    """Mirrors E3's extraction output, with one deliberate change: no
    display_code (server-computed, a counter the model has no way to know)
    and no real source_chunk_id. E1: "No agent may fabricate IDs" - a batch
    covers several chunks, so the model needs *some* way to say which one an
    item came from, but a real UUID is exactly the kind of value a model
    might paraphrase or invent wrong. source_chunk_ref is a short label
    (batch.py assigns "chunk_1", "chunk_2", ...) scoped to just this one
    call's prompt; extract.py resolves it back to the real UUID after
    parsing, so the model never has to get a UUID right.
    """

    requirement_text: str
    category: RequirementCategory
    mandatory: bool
    source_chunk_ref: str
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedSubmissionItem(BaseModel):
    name: str
    mandatory: bool
    source_chunk_ref: str | None = None


class ExtractedClarification(BaseModel):
    question: str
    reason: str | None = None
    source_chunk_ref: str | None = None


class ExtractionOutput(BaseModel):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    submission_items: list[ExtractedSubmissionItem] = Field(default_factory=list)
    clarifications: list[ExtractedClarification] = Field(default_factory=list)


class VerificationOutput(BaseModel):
    """Mirrors E3's verification output minus requirement_id - verify.py
    already knows which requirement it asked about (it's the call's own
    input, one call per requirement), so the model is never asked to echo an
    ID back at all, not even a low-risk one."""

    supported_by_source: bool
    mandatory_language_supported: bool
    verification_confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
