import uuid

# TenderCreated is a B2 engineering choice, not a literal Build Guide requirement -
# Part G2's text only mandates an outbox event for document upload (a later block).
# This gives the outbox mechanism a real trigger ahead of any real consumer.
EVENT_TENDER_CREATED = "TenderCreated"

AGGREGATE_TENDER = "tender"


def tender_created_payload(tender_id: uuid.UUID, organization_id: uuid.UUID, name: str) -> dict:
    return {
        "tender_id": str(tender_id),
        "organization_id": str(organization_id),
        "name": name,
    }


def tender_created_idempotency_key(tender_id: uuid.UUID) -> str:
    return f"tender_created:{tender_id}"


# R1's Request Flow diagram names both of these explicitly: workflow-run creation
# and a gate decision are the two business changes that must commit atomically
# with an outbox row, so the dispatcher can reliably hand them to RQ afterward.
EVENT_WORKFLOW_RUN_CREATED = "WorkflowRunCreated"
EVENT_GATE_DECISION_RECORDED = "GateDecisionRecorded"

AGGREGATE_WORKFLOW_RUN = "workflow_run"
AGGREGATE_APPROVAL_GATE = "approval_gate"


def workflow_run_created_payload(workflow_run_id: uuid.UUID, tender_id: uuid.UUID) -> dict:
    return {
        "workflow_run_id": str(workflow_run_id),
        "tender_id": str(tender_id),
    }


def workflow_run_created_idempotency_key(workflow_run_id: uuid.UUID) -> str:
    return f"workflow_run_created:{workflow_run_id}"


def gate_decision_recorded_payload(gate_id: uuid.UUID, workflow_run_id: uuid.UUID) -> dict:
    return {
        "gate_id": str(gate_id),
        "workflow_run_id": str(workflow_run_id),
    }


def gate_decision_recorded_idempotency_key(gate_id: uuid.UUID) -> str:
    # A gate is only ever written to (status leaves 'pending') once - the
    # idempotent-replay branch in gate_service.record_decision returns early
    # without touching the outbox on every decision after the first, so this
    # key never needs a second identifying segment the way the RQ resume
    # job_id does (that one is keyed by decision content too - see I3).
    return f"gate_decision_recorded:{gate_id}"
