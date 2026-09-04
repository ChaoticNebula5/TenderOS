from datetime import datetime
from typing import Annotated, Literal, Union
from uuid import UUID

from pydantic import BaseModel, Field

from tenderos_api.schemas.readiness import ReadinessBlocker, ReadinessCounts


class TargetUpdate(BaseModel):
    # B12: real per-target application, closing the gap this field carried
    # since B3 (see KNOWN_LIMITATIONS.md's now-resolved target_updates
    # entry). target_type intentionally NOT constrained to gate_target_
    # service._KNOWN_TARGET_TYPES here - apply_target_update's own
    # permissive-on-unknown-type posture (forward-compatibility) would be
    # defeated if Pydantic rejected an unrecognized type before it ever
    # reached that function. decision is narrower (Literal["approve"])
    # because that's the only value this block's contract defines at all,
    # not a forward-compatibility case - not a security boundary either
    # way, since gate_target_service re-validates both regardless of what
    # Pydantic already let through.
    target_type: str
    target_id: UUID
    decision: Literal["approve"]


class GateDecisionRequest(BaseModel):
    # Narrowed from `str` in B13 - closes the pre-B12-audit minor finding
    # "invalid gate decisions produce a bare 500, not 422" (gate_service.
    # record_decision looked up an unrecognized value in _DECISION_TO_
    # STATUS and KeyError'd past any handler). FastAPI now rejects an
    # invalid decision at the request-validation layer, before this ever
    # reaches the service. Matches orchestration/gates.py's own
    # ALLOWED_DECISIONS tuple - restated literally rather than imported,
    # same convention TargetUpdate.decision below already uses.
    decision: Literal["approve", "reject", "request_changes"]
    target_updates: list[TargetUpdate] = []
    notes: str | None = None
    # B12: only meaningful for gate_type="final_readiness" with
    # decision="approve" - see routes/gates.py's decide_gate. "refuses
    # approval until resolved or explicitly waived by authorized actor"
    # (Build Guide stop-gate, verbatim) - this is the explicit act. Ignored
    # for every other gate type/decision combination, not just permitted
    # but meaningless there (no readiness concept applies).
    waive: bool = False


class GateTargetRef(BaseModel):
    # The one shape every gate_type's targets list actually uses (all six
    # producers confirmed by reading orchestration/nodes/*.py directly -
    # requirement.py, compliance.py, talent.py, commercial.py, redteam.py,
    # final_gate.py). final_readiness's targets is always [] - still this
    # same element type, just an empty list of it, not a different shape.
    target_type: str
    target_id: UUID


# Five gate types build a summary that's nothing but named integer counts -
# each confirmed against its own node function's literal
# build_gate_payload(...) call, not assumed uniform from any two of them.
class RequirementReviewSummary(BaseModel):
    requirement_count: int
    mandatory_count: int
    unverified_mandatory_count: int
    submission_item_count: int
    clarification_count: int


class ComplianceReviewSummary(BaseModel):
    compliance_entry_count: int
    pending_count: int
    high_risk_count: int


class ResourceSelectionSummary(BaseModel):
    role_count: int
    candidate_count: int
    unselected_count: int


class CommercialApprovalSummary(BaseModel):
    finding_count: int
    pending_count: int
    critical_count: int


class FindingResolutionSummary(BaseModel):
    open_finding_count: int
    critical_count: int


class CostBudgetSummary(BaseModel):
    # I5's gate (Build Guide :2755-2763) - see cost_budget_gate_node's own
    # summary dict, which this mirrors field-for-field. Not a count of
    # objects like the five summaries above - a snapshot of the run's own
    # spend against its budget.
    accumulated_cost_usd: float
    budget_usd: float
    overage_usd: float


class FinalReadinessSummary(BaseModel):
    # The one gate type whose summary isn't a plain count dict - it carries
    # the readiness snapshot final_gate_node read moments earlier
    # (final_gate.py:61-74), reusing the exact same Blocker/Counts models
    # readiness.py's own report response is typed against, since it's
    # populated from that same ReadinessResult.
    #
    # counts is Optional, not ReadinessCounts outright - caught by actually
    # validating this model against final_gate_node's own no-report branch
    # (`report.counts if report else {}`, final_gate.py:70-72) before
    # trusting the type, not by reading the node's code alone. That branch
    # sends a bare {}, which a required-fields ReadinessCounts rejects
    # outright - and defaulting every field to 0 instead would have been
    # actively misleading (a bid manager reading "requirements_total: 0"
    # here can't tell "genuinely zero requirements" from "no report ever
    # ran," and readiness_report_id being unset only happens when
    # readiness_generation_node itself failed - exactly the case where
    # silently showing zeroes is least acceptable). None preserves the
    # real distinction final_gate_node's own conditional is drawing.
    ready: bool
    blockers: list[ReadinessBlocker]
    counts: ReadinessCounts | None
    degraded_stages: list[str]


class _GateResponseBase(BaseModel):
    gate_id: UUID
    workflow_run_id: UUID
    status: str
    targets: list[GateTargetRef]
    allowed_decisions: list[str]
    decision_payload: dict | None
    decided_by: UUID | None
    requested_at: datetime
    decided_at: datetime | None


# Six subclasses, discriminated on gate_type, rather than one flat model
# with summary: dict - the whole point of pinning these shapes was to stop
# handing the gate-control component (and anyone reading the OpenAPI
# schema) an untyped dict it has to reverse-engineer from
# gate_target_service.py. gate_type is a Literal of exactly one string
# per class, which is what Field(discriminator=...) below switches on.
class RequirementReviewGateResponse(_GateResponseBase):
    gate_type: Literal["requirement_review"]
    summary: RequirementReviewSummary


class ComplianceReviewGateResponse(_GateResponseBase):
    gate_type: Literal["compliance_review"]
    summary: ComplianceReviewSummary


class ResourceSelectionGateResponse(_GateResponseBase):
    gate_type: Literal["resource_selection"]
    summary: ResourceSelectionSummary


class CommercialApprovalGateResponse(_GateResponseBase):
    gate_type: Literal["commercial_approval"]
    summary: CommercialApprovalSummary


class FindingResolutionGateResponse(_GateResponseBase):
    gate_type: Literal["finding_resolution"]
    summary: FindingResolutionSummary


class FinalReadinessGateResponse(_GateResponseBase):
    gate_type: Literal["final_readiness"]
    summary: FinalReadinessSummary


class CostBudgetGateResponse(_GateResponseBase):
    gate_type: Literal["cost_budget"]
    summary: CostBudgetSummary


GateResponse = Annotated[
    Union[
        RequirementReviewGateResponse,
        ComplianceReviewGateResponse,
        ResourceSelectionGateResponse,
        CommercialApprovalGateResponse,
        FindingResolutionGateResponse,
        FinalReadinessGateResponse,
        CostBudgetGateResponse,
    ],
    Field(discriminator="gate_type"),
]
