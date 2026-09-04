import enum
from typing import Literal


class Role(enum.StrEnum):
    BID_MANAGER = "bid_manager"
    SOLUTION_ARCHITECT = "solution_architect"
    TALENT_REVIEWER = "talent_reviewer"
    COMMERCIAL_REVIEWER = "commercial_reviewer"
    PROPOSAL_WRITER = "proposal_writer"
    APPROVER = "approver"
    ADMIN = "admin"


# R3's fine-grained in-graph workflow status, transcribed verbatim. Lives here
# (not in orchestration/state.py, which re-exports it) so domain services can
# reference it without importing from orchestration - A4.1's dependency
# direction is orchestration -> domain services, never the reverse.
#
# "awaiting_cost_budget_review" is a real addition beyond R3's original
# verbatim list, not a transcription - I5 (Build Guide :2755-2763) names the
# cost-budget gate but R3's own status enum predates it. Same precedent as
# rbac.py's own "cancel_workflow_run" addition (a real gap Part H's own
# locked table never named, added with the reasoning on record rather than
# silently folded in as if it had always been there).
WorkflowStatus = Literal[
    "created",
    "extracting_requirements",
    "awaiting_requirement_review",
    "planning",
    "awaiting_cost_budget_review",
    "running_specialists",
    "awaiting_compliance_review",
    "awaiting_resource_selection",
    "awaiting_commercial_approval",
    "drafting",
    "projecting_graph",
    "red_teaming",
    "awaiting_finding_resolution",
    "generating_readiness",
    "awaiting_final_approval",
    "ready",
    "rejected",
    "changes_requested",
    "failed",
]

# workflow_runs.status (Part C's CHECK constraint) is a coarse 6-value column;
# WorkflowStatus above is the graph's fine-grained internal status. Nodes update
# TenderState.status on every step but the Postgres row - what routes/workflow.py
# actually reads back for GET /workflow-runs/{id} - only needs the coarse view.
# This mapping is "workflow status synchronization" (its own B3 Build-list item).
_AWAITING_HUMAN: frozenset[WorkflowStatus] = frozenset(
    {
        "awaiting_requirement_review",
        "awaiting_cost_budget_review",
        "awaiting_compliance_review",
        "awaiting_resource_selection",
        "awaiting_commercial_approval",
        "awaiting_finding_resolution",
        "awaiting_final_approval",
        # A gate's own status is "changes_requested" (a real, valid
        # ALLOWED_DECISIONS value), but the run itself is not rejected and
        # not approved - a human's decision is still the last word pending
        # further action. "waiting_for_human" is the more honest coarse
        # reading than "failed" (nothing errored) or "running" (nothing is
        # advancing) - see the gate nodes' own request_changes handling.
        "changes_requested",
    }
)
_TERMINAL: dict[WorkflowStatus, str] = {
    "ready": "completed",
    "rejected": "failed",
    "failed": "failed",
}


def coarse_status_for(status: WorkflowStatus) -> str:
    if status in _AWAITING_HUMAN:
        return "waiting_for_human"
    if status in _TERMINAL:
        return _TERMINAL[status]
    if status == "created":
        return "queued"
    return "running"
