from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["critical", "major", "minor"]


class RedteamCritiqueFinding(BaseModel):
    """Mirrors E8's redteam_findings JSON shape, with the same "no
    fabricated IDs" omission every other agent schema in this project
    already applies (agents/schemas/compliance.py's evidence_refs,
    requirements.py's VerificationOutput): the model never sees or invents
    a real proposal_claim/evidence_link UUID, only the caller-assigned
    labels shown in the prompt (claim_1, evidence_1, ...). agents/redteam/
    critique.py resolves target_claim_label/evidence_refs back to real IDs
    after parsing - this schema has no field a red-team writer could use
    to mutate the target itself (no revised_text/corrected_claim field
    anywhere), which is the structural half of "Red Team cannot silently
    edit approved content."
    """

    target_claim_label: str
    finding_category: str
    severity: Severity
    finding_text: str
    violated_condition: str
    rebuttal: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class RedteamCritique(BaseModel):
    findings: list[RedteamCritiqueFinding] = Field(default_factory=list)
