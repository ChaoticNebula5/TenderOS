from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# The 12 blocker categories readiness_service.py actually produces
# (the constants atop readiness_service.py - the Build Guide's 7 required
# categories plus this project's own red_team_review_incomplete, since plan
# item 86 selected_employee_removed, since plan item 99
# document_not_parsed, since plan item 104 document_not_extracted, and since
# plan item 121 reopened_compliance_decision). Restated
# here as literal strings rather than imported, matching this schema
# module's existing convention (schemas/gates.py's GateDecisionRequest.
# decision does the same for its three values) - a new category added to that file without
# a matching update here fails closed (422/500 on the response, not a
# silently-accepted new value), which is the right direction to fail.
_BlockerCategory = Literal[
    "unresolved_mandatory_requirement",
    "compliant_decision_without_valid_evidence",
    "critical_redteam_finding",
    "missing_mandatory_submission_item",
    "unapproved_required_commercial_item",
    "role_with_no_selected_candidate",
    "stale_graph_projection",
    "red_team_review_incomplete",
    "selected_employee_removed",
    "document_not_parsed",
    "document_not_extracted",
    # Plan item 121: a reviewer's compliance decision a changed analysis
    # answer reopened.
    "reopened_compliance_decision",
]


class ReadinessBlocker(BaseModel):
    category: _BlockerCategory
    target_type: str
    target_id: str | None
    message: str


class ReadinessCounts(BaseModel):
    # Exactly the keys evaluate_readiness always sets, in one
    # unconditional pass (readiness_service.py:184-319) - pinned against a
    # real persisted report in test_readiness_service.py, not read off this
    # docstring's own claim.
    requirements_total: int
    requirements_completed: int
    mandatory_requirements_unresolved: int
    compliance_decisions_awaiting_approval: int
    requirements_lacking_valid_evidence: int
    critical_major_redteam_findings: int
    missing_submission_artefacts: int
    commercial_findings_open: int
    resource_gaps: int
    proposal_claims_needing_review: int
    open_clarifications: int
    # Plan item 86: selected employees removed from the roster. None on a
    # report stored before this count existed, which never counted them.
    selected_employees_removed: int | None = None
    # Plan item 99: tender documents not parsed. None on a report stored
    # before this count existed.
    documents_not_parsed: int | None = None
    # Plan item 104: parsed tender documents no completed extraction has read
    # in full, or with no readable text. None on a report stored before this
    # count existed.
    documents_not_extracted: int | None = None
    # Plan item 121: reviewers' compliance decisions a changed analysis
    # answer reopened. None on a report stored before this count existed.
    compliance_decisions_reopened: int | None = None


class ReadinessReportResponse(BaseModel):
    id: UUID
    tender_id: UUID
    tender_state_version: int
    counts: ReadinessCounts
    blockers: list[ReadinessBlocker]
    ready: bool
    generated_at: datetime

    model_config = {"from_attributes": True}
