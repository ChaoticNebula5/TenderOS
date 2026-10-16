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


def document_uploaded_payload(
    document_id: uuid.UUID,
    tender_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
) -> dict:
    # _document_uploaded_dispatch (outbox_dispatcher.py) only ever reads
    # document_id from this payload, so tender_id/organization_id here are
    # informational (visible in the outbox_events row for debugging/audit),
    # not load-bearing for routing - safe for either to be absent depending
    # on which scope uploaded the document.
    payload: dict = {"document_id": str(document_id)}
    if tender_id is not None:
        payload["tender_id"] = str(tender_id)
    if organization_id is not None:
        payload["organization_id"] = str(organization_id)
    return payload


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


# B6's knowledge-import upload routes (G6) create a Document (which already
# fires DocumentUploaded above, triggering ingestion) plus a
# knowledge_import_batches row - this second event is what triggers the
# actual mechanical/LLM import step (parsing an employee-master row,
# extracting a resume profile, ...) once that document exists. Reuses
# AGGREGATE_DOCUMENT since the event is still fundamentally "this document
# is ready to be processed," just for a different processing pipeline than
# plain ingestion - not a new aggregate for what is still a document-shaped
# event.
EVENT_KNOWLEDGE_DOCUMENT_UPLOADED = "KnowledgeDocumentUploaded"


def knowledge_document_uploaded_payload(document_id: uuid.UUID, batch_id: uuid.UUID) -> dict:
    return {"document_id": str(document_id), "batch_id": str(batch_id)}


def knowledge_document_uploaded_idempotency_key(
    document_id: uuid.UUID, request_id: uuid.UUID | None = None
) -> str:
    # request_id is None for a genuinely new upload (document_id alone is
    # already unique - one document is only ever freshly uploaded once).
    # A caller re-queuing processing against an EXISTING document (B6's
    # replace_employee_resume, when the "new" resume content happens to
    # match one already on file for this org - documents.uq_org_document_hash
    # means that reuses the existing row rather than inserting a duplicate)
    # must pass a fresh one, or this collides with that document's original
    # upload event on the unique idempotency_key constraint - caught live,
    # same "fresh identifier per call" fix reprocess_document already needed
    # for the plain DocumentUploaded event.
    suffix = f":{request_id}" if request_id is not None else ""
    return f"knowledge_document_uploaded:{document_id}{suffix}"


# B14. set_knowledge_selection's own docstring has named this flow
# aspirationally since B6 ("outbox ImpactReanalysisRequested"), but Part J's
# own B14 build list names the event KnowledgeSelectionChanged - that literal
# name is used here, since it's the one actually naming this specific event
# in the block that owns building it, not a paraphrase in an older
# docstring. See KNOWN_LIMITATIONS.md for the B14 outbox-vocabulary
# inventory this event is the one real, wired instance of.
EVENT_KNOWLEDGE_SELECTION_CHANGED = "KnowledgeSelectionChanged"

AGGREGATE_KNOWLEDGE_SELECTION = "knowledge_selection"


def knowledge_selection_changed_payload(
    tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, selected: bool, state_version: int
) -> dict:
    return {
        "tender_id": str(tender_id),
        "knowledge_item_id": str(knowledge_item_id),
        "selected": selected,
        "state_version": state_version,
    }


def knowledge_selection_changed_idempotency_key(tender_id: uuid.UUID, state_version: int) -> str:
    # Keyed on the tender's own new state_version, not the selection row's
    # id - state_version already uniquely identifies this specific mutation
    # (Part C's own state-version invariant), and reusing it here ties this
    # outbox row to the same identifier the reanalysis job's own RQ job_id
    # uses (reanalyze_{tender_id}_{state_version}, outbox_dispatcher.py)
    # rather than inventing a second one for the same event.
    return f"knowledge_selection_changed:{tender_id}:{state_version}"


# The two "outcome" events the reanalysis job (apps/worker/jobs/
# reanalysis.py) emits per affected requirement/claim it reevaluates -
# durable, inspectable records of what the job actually did, matching this
# project's own outbox-as-source-of-truth pattern (KNOWN_LIMITATIONS.md's
# B14 SSE-transport decision). Not currently routed to an RQ job by
# outbox_dispatcher.py - falls to _default_dispatch, the same "declared,
# no real consumer yet" shape TenderCreated has always used. Idempotency
# keys include state_version for the same reason
# knowledge_selection_changed's own does: two different selection changes
# affecting the same requirement/claim must not collide on the same key.
EVENT_COMPLIANCE_CHANGED = "ComplianceChanged"
EVENT_PROPOSAL_CHANGED = "ProposalChanged"

AGGREGATE_COMPLIANCE_ENTRY = "compliance_entry"
AGGREGATE_PROPOSAL_CLAIM = "proposal_claim"


def compliance_changed_payload(
    compliance_entry_id: uuid.UUID, requirement_id: uuid.UUID, tender_id: uuid.UUID, status: str
) -> dict:
    return {
        "compliance_entry_id": str(compliance_entry_id),
        "requirement_id": str(requirement_id),
        "tender_id": str(tender_id),
        "status": status,
    }


def compliance_changed_idempotency_key(requirement_id: uuid.UUID, state_version: int) -> str:
    return f"compliance_changed:{requirement_id}:{state_version}"


def proposal_changed_payload(
    claim_id: uuid.UUID,
    tender_id: uuid.UUID,
    grounding_status: str,
    evidence_link_ids_considered: list[uuid.UUID],
) -> dict:
    return {
        "claim_id": str(claim_id),
        "tender_id": str(tender_id),
        "grounding_status": grounding_status,
        "evidence_link_ids_considered": [str(i) for i in evidence_link_ids_considered],
    }


def proposal_changed_idempotency_key(claim_id: uuid.UUID, state_version: int) -> str:
    return f"proposal_changed:{claim_id}:{state_version}"


# B14's own workflow-task/run progress events - what the SSE route (GET
# .../events/stream, apps/api/routes/workflow.py) actually polls the outbox
# for. Written from inside db/repositories/workflow.py's own 4 methods
# (update_run_status, create_task, mark_task_completed, mark_task_failed),
# not from each of their ~36 real call sites across orchestration/nodes/ -
# see that repository's own module docstring for why. None of these four
# have a natural version counter the way tenders.state_version does
# (KnowledgeSelectionChanged/ComplianceChanged/ProposalChanged all key off
# it above), so every idempotency_key here includes a fresh uuid4 suffix -
# true dedup isn't needed for a repeatable wake-up ping, and a same-key
# collision on a legitimate retry would raise IntegrityError inside the
# same transaction as the real state write these events describe, which
# must never happen. WorkflowTaskCreated is the one exception - task.id is
# always genuinely fresh when create_task's own body runs (callers check
# get_task_by_key first), so no suffix is needed there.
EVENT_WORKFLOW_RUN_STATUS_CHANGED = "WorkflowRunStatusChanged"
EVENT_WORKFLOW_TASK_CREATED = "WorkflowTaskCreated"
EVENT_WORKFLOW_TASK_COMPLETED = "WorkflowTaskCompleted"
EVENT_WORKFLOW_TASK_FAILED = "WorkflowTaskFailed"

AGGREGATE_WORKFLOW_TASK = "workflow_task"


def workflow_run_status_changed_payload(workflow_run_id: uuid.UUID, status: str) -> dict:
    # status is informational (visible on the row for debugging), same
    # "not load-bearing" posture document_uploaded_payload's own
    # tender_id/organization_id already take - the SSE route re-queries
    # workflow_runs for current status rather than trusting this copy.
    return {"workflow_run_id": str(workflow_run_id), "status": status}


def workflow_run_status_changed_idempotency_key(workflow_run_id: uuid.UUID) -> str:
    return f"workflow_run_status_changed:{workflow_run_id}:{uuid.uuid4()}"


def workflow_task_created_payload(
    workflow_run_id: uuid.UUID, task_id: uuid.UUID, task_type: str
) -> dict:
    return {
        "workflow_run_id": str(workflow_run_id),
        "task_id": str(task_id),
        "task_type": task_type,
    }


def workflow_task_created_idempotency_key(task_id: uuid.UUID) -> str:
    return f"workflow_task_created:{task_id}"


def workflow_task_completed_payload(
    workflow_run_id: uuid.UUID, task_id: uuid.UUID, task_type: str
) -> dict:
    return {
        "workflow_run_id": str(workflow_run_id),
        "task_id": str(task_id),
        "task_type": task_type,
    }


def workflow_task_completed_idempotency_key(task_id: uuid.UUID) -> str:
    return f"workflow_task_completed:{task_id}:{uuid.uuid4()}"


def workflow_task_failed_payload(
    workflow_run_id: uuid.UUID, task_id: uuid.UUID, task_type: str
) -> dict:
    return {
        "workflow_run_id": str(workflow_run_id),
        "task_id": str(task_id),
        "task_type": task_type,
    }


def workflow_task_failed_idempotency_key(task_id: uuid.UUID) -> str:
    return f"workflow_task_failed:{task_id}:{uuid.uuid4()}"


# Phase 4: async operations (db/models/async_operation.py). OperationRequested
# is routed by the outbox dispatcher to the worker's run_operation job;
# OperationStatusChanged is what the operation's SSE stream pushes to the web
# app (routes/operations.py), one per status the operation reaches.
AGGREGATE_ASYNC_OPERATION = "async_operation"
EVENT_OPERATION_REQUESTED = "OperationRequested"
EVENT_OPERATION_STATUS_CHANGED = "OperationStatusChanged"


def operation_requested_payload(operation_id: uuid.UUID, kind: str) -> dict:
    # kind is read by the dispatcher to pick the job timeout for this kind.
    return {"operation_id": str(operation_id), "kind": kind}


def operation_requested_idempotency_key(operation_id: uuid.UUID) -> str:
    # Exactly one request event per operation.
    return f"operation_requested:{operation_id}"


def operation_status_changed_payload(operation_id: uuid.UUID, status: str) -> dict:
    # Informational, like workflow_run_status_changed_payload: the stream's
    # client re-reads the operation for its current status.
    return {"operation_id": str(operation_id), "status": status}


def operation_status_changed_idempotency_key(operation_id: uuid.UUID, status: str) -> str:
    # An operation reaches each status at most once (queued, running, then
    # succeeded or failed), so the status completes the key.
    return f"operation_status_changed:{operation_id}:{status}"
