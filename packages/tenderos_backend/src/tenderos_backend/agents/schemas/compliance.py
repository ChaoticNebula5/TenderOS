from typing import Literal

from pydantic import BaseModel, Field

# Deliberately excludes 'not_reviewed' - that's the entry's pre-analysis
# default (Part C), never something the model is asked to propose.
ComplianceStatus = Literal[
    "fully_compliant",
    "partially_compliant",
    "non_compliant",
    "clarification_required",
    "evidence_unavailable",
]


class ComplianceProposal(BaseModel):
    """Mirrors E4's compliance-proposal JSON, with the same two omissions
    VerificationOutput (agents/schemas/requirements.py) already established
    for the same reasons: no requirement_id (one call is always about one
    already-known requirement, never asked to echo it back), and evidence
    referenced by short label rather than raw UUID (E1: "no fabricated
    IDs" - propose.py hands the model a labeled list of retrieved evidence
    candidates, e.g. "evidence_1", and resolves the labels it cites back to
    real evidence_link UUIDs after parsing, so the model is never in a
    position to invent or misremember one).

    clarification_required is independent of status, not implied by it - a
    proposal can be partially_compliant and still flag that one point needs
    human clarification (E4's own example does exactly this).
    """

    status: ComplianceStatus
    evidence_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_summary: str
    clarification_required: bool = False
    clarification_question: str | None = None
