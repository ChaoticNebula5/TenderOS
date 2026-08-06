import uuid

# R5's canonical gate list, transcribed verbatim - also the CHECK constraint's
# allowed values on approval_gates.gate_type (migration 002_workflow).
GATE_TYPES = (
    "requirement_review",
    "compliance_review",
    "resource_selection",
    "commercial_approval",
    "finding_resolution",
    "final_readiness",
)

# Part H's RBAC matrix, one row per gate type. Only "requirement_review" and
# "final_readiness" have a real producer in B3 (the other four gate types are
# valid rows in this table with no caller yet - same "table is locked and
# small, later blocks reference it" precedent as B2's rbac.ACTION_ROLES).
GATE_TYPE_ACTIONS: dict[str, str] = {
    "requirement_review": "review_requirements",
    "compliance_review": "approve_compliance",
    "resource_selection": "select_resource",
    "commercial_approval": "resolve_commercial_finding",
    "finding_resolution": "resolve_redteam_finding",
    "final_readiness": "final_readiness_approval",
}

ALLOWED_DECISIONS = ("approve", "reject", "request_changes")

_DECISION_TO_STATUS = {
    "approve": "approved",
    "reject": "rejected",
    "request_changes": "changes_requested",
}


def action_for_gate_type(gate_type: str) -> str:
    return GATE_TYPE_ACTIONS[gate_type]


def status_for_decision(decision: str) -> str:
    return _DECISION_TO_STATUS[decision]


def derive_gate_key(gate_type: str, plan_version: int) -> str:
    # R5 example: "compliance_review:plan-3" - deterministic per workflow stage
    # and revision, so re-opening the same logical gate on a replan reuses the
    # same key instead of creating a duplicate row (uq_gate_key is on
    # (workflow_run_id, gate_key)).
    return f"{gate_type}:plan-{plan_version}"


def build_gate_payload(
    gate_id: uuid.UUID,
    gate_type: str,
    workflow_run_id: uuid.UUID,
    targets: list[dict],
    summary: dict,
) -> dict:
    # Matches R5's gate payload JSON shape exactly. Takes raw fields, not a
    # persisted ApprovalGate, so callers can build this before the row exists
    # (gate_id is caller-generated - see GateRepository.create) and reuse it
    # unchanged as the row's own request_payload.
    return {
        "gate_id": str(gate_id),
        "gate_type": gate_type,
        "workflow_run_id": str(workflow_run_id),
        "targets": targets,
        "summary": summary,
        "allowed_decisions": list(ALLOWED_DECISIONS),
    }
