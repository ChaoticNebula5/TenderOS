from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from tenderos_api.schemas.readiness import ReadinessBlocker, ReadinessCounts


class GateDecisionRequest(BaseModel):
    # Narrowed from `str` in B13 - closes the pre-B12-audit minor finding
    # "invalid gate decisions produce a bare 500, not 422" (gate_service.
    # record_decision looked up an unrecognized value in _DECISION_TO_
    # STATUS and KeyError'd past any handler). FastAPI now rejects an
    # invalid decision at the request-validation layer, before this ever
    # reaches the service. Matches orchestration/gates.py's own
    # ALLOWED_DECISIONS tuple - restated literally rather than imported.
    decision: Literal["approve", "reject", "request_changes"]
    # PR 16: a decision decides the gate only, and routes/gates.py refuses any
    # entry here with 422 GATE_TARGET_UPDATES, on every gate. Kept, untyped,
    # so the spec's example payload (an empty list) is still accepted and any
    # entry reaches that one answer rather than a field-shape complaint.
    target_updates: list[dict[str, Any]] = []
    notes: str | None = None
    # B12: only meaningful for gate_type="final_readiness" with
    # decision="approve" - see routes/gates.py's decide_gate. The Build
    # Guide's stop-gate refuses approval until every blocker is resolved or
    # an authorized actor explicitly waives it - this is the explicit act. Ignored
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
    # The total of the two below.
    unverified_mandatory_count: int
    submission_item_count: int
    clarification_count: int
    # Plan item 59, Q5 (PR 21): mandatory requirements not checked yet, and
    # those whose check failed. None on a gate opened before these existed;
    # its snapshot has only the total.
    mandatory_not_verified_yet_count: int | None = None
    mandatory_verification_failed_count: int | None = None


class ComplianceReviewSummary(BaseModel):
    compliance_entry_count: int
    pending_count: int
    high_risk_count: int


class ResourceSelectionSummary(BaseModel):
    role_count: int
    # Both count (role, candidate) matches, not people or roles.
    candidate_count: int
    unselected_count: int
    # Plan item 73: the roles with no human-selected candidate, what the
    # panel shows as "Roles without a pick". None on a gate opened before
    # this field existed; its snapshot never counted roles.
    unselected_role_count: int | None = None
    # Plan item 86: selections whose employee is archived now. None on a
    # gate opened before this field existed, the same way.
    archived_selected_count: int | None = None


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


# Plan item 73, decision 3: every gate type but final_readiness also
# carries live_summary, its counts as they are now (gate_summary_service),
# for a pending gate only; None once decided. `summary` stays the snapshot
# taken when the gate opened, the record of what the reviewer was shown.
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
# schema) an untyped dict it has to reverse-engineer. gate_type is a Literal of exactly one string
# per class, which is what Field(discriminator=...) below switches on.
class RequirementReviewGateResponse(_GateResponseBase):
    gate_type: Literal["requirement_review"]
    summary: RequirementReviewSummary
    live_summary: RequirementReviewSummary | None = None


class ComplianceReviewGateResponse(_GateResponseBase):
    gate_type: Literal["compliance_review"]
    summary: ComplianceReviewSummary
    live_summary: ComplianceReviewSummary | None = None


class ResourceSelectionGateResponse(_GateResponseBase):
    gate_type: Literal["resource_selection"]
    summary: ResourceSelectionSummary
    live_summary: ResourceSelectionSummary | None = None


class CommercialApprovalGateResponse(_GateResponseBase):
    gate_type: Literal["commercial_approval"]
    summary: CommercialApprovalSummary
    live_summary: CommercialApprovalSummary | None = None


class FindingResolutionGateResponse(_GateResponseBase):
    gate_type: Literal["finding_resolution"]
    summary: FindingResolutionSummary
    live_summary: FindingResolutionSummary | None = None


class FinalReadinessGateResponse(_GateResponseBase):
    gate_type: Literal["final_readiness"]
    summary: FinalReadinessSummary


class CostBudgetGateResponse(_GateResponseBase):
    gate_type: Literal["cost_budget"]
    summary: CostBudgetSummary
    live_summary: CostBudgetSummary | None = None


GateResponse = Annotated[
    (
        RequirementReviewGateResponse
        | ComplianceReviewGateResponse
        | ResourceSelectionGateResponse
        | CommercialApprovalGateResponse
        | FindingResolutionGateResponse
        | FinalReadinessGateResponse
        | CostBudgetGateResponse
    ),
    Field(discriminator="gate_type"),
]
