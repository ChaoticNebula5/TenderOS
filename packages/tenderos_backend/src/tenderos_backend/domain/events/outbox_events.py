import uuid

# TenderCreated is a B2 engineering choice, not a literal Build Guide requirement -
# Part G2's text only mandates an outbox event for document upload (a later block).
# This gives the outbox mechanism a real trigger ahead of any real consumer.
EVENT_TENDER_CREATED = "TenderCreated"

AGGREGATE_TENDER = "tender"

# G2 step 5: "Commit ingestion outbox event." This is the "later block" the
# comment above already named - B14 later lists "DocumentUploaded" among its
# own outbox events for SSE dispatch, but B4 is the real first owner: this is
# what triggers the ingestion RQ job, same role WorkflowRunCreated plays for
# the workflow queue.
EVENT_DOCUMENT_UPLOADED = "DocumentUploaded"

AGGREGATE_DOCUMENT = "document"


def document_uploaded_payload(document_id: uuid.UUID, tender_id: uuid.UUID) -> dict:
    return {
        "document_id": str(document_id),
        "tender_id": str(tender_id),
    }


def document_uploaded_idempotency_key(document_id: uuid.UUID) -> str:
    return f"document_uploaded:{document_id}"


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


# G5's POST /requirements/{id}/verify - a human asking, on demand, for the
# extract-then-verify agent to (re-)check one specific requirement. Line 538
# forbids running an LLM call inside a FastAPI request, so this route can only
# ever write this event and return; the worker's outbox_dispatcher does the
# actual verification call, same "commit the intent, let the dispatcher hand
# it to RQ" shape DocumentUploaded/WorkflowRunCreated already use.
EVENT_REQUIREMENT_VERIFICATION_REQUESTED = "RequirementVerificationRequested"

AGGREGATE_REQUIREMENT = "requirement"


def requirement_verification_requested_payload(requirement_id: uuid.UUID) -> dict:
    return {"requirement_id": str(requirement_id)}


def requirement_verification_requested_idempotency_key(
    requirement_id: uuid.UUID, request_id: uuid.UUID
) -> str:
    # Unlike gate decisions (write-once) or document upload (SHA-256 already
    # dedups), a human may legitimately ask to re-verify the same requirement
    # more than once (e.g. after editing its text) - request_id makes each
    # call its own outbox row instead of colliding on a bare requirement_id
    # key, mirroring reprocess_document's "fresh identifier per call" choice.
    return f"requirement_verification_requested:{requirement_id}:{request_id}"
