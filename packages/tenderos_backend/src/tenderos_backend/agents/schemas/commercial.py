from typing import Literal

from pydantic import BaseModel

Severity = Literal["critical", "major", "minor"]


class FindingExplanation(BaseModel):
    """E6's own JSON example shows expected_value/actual_value/delta as
    fields the LLM's output carries - but E6's own Rules section forbids
    exactly that ("The LLM may not invent expected_value, actual_value, or
    delta"). Rather than ask the model to echo back numbers pricing_checks.py
    already computed (the same fabrication risk B7/B8A already solved once
    by never handing the model a real UUID to restate), this call is scoped
    to ONE already-computed finding at a time (mirrors propose_compliance/
    score_candidate's one-call-per-target shape) and the model is never
    asked to state a number at all - only to classify severity (Build
    Guide: "classify issues" is an explicitly allowed LLM job) and write a
    human-readable explanation. commercial_service.py assembles the final
    persisted finding by combining this output with the deterministic
    PricingFindingDraft it was generated from, never the reverse.
    """

    severity: Severity
    explanation: str
