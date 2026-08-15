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
WorkflowStatus = Literal[
    "created",
    "extracting_requirements",
    "awaiting_requirement_review",
    "planning",
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
        "awaiting_compliance_review",
        "awaiting_resource_selection",
        "awaiting_commercial_approval",
        "awaiting_finding_resolution",
        "awaiting_final_approval",
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
