from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class WorkflowRunResponse(BaseModel):
    id: UUID
    tender_id: UUID
    langgraph_thread_id: str
    status: str
    plan_version: int
    prompt_bundle_version: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    # Plan item 19: why a failed run failed (WORKER_LOST, DISPATCH_FAILED,
    # JOB_TIMEOUT), None for any other run. Defaulted so an idempotent
    # replay of a body cached before this field existed still validates.
    error_code: str | None = None

    model_config = {"from_attributes": True}


class AuditEventResponse(BaseModel):
    id: UUID
    actor_type: str
    actor_id: str
    action: str
    target_type: str | None
    target_id: UUID | None
    before: dict | None
    after: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SSETicketResponse(BaseModel):
    """B15: minted by POST /workflow-runs/{run_id}/events/ticket, consumed
    by GET .../events/stream in place of the real access JWT - see
    sse_tickets.py's own module docstring."""

    ticket: str
    expires_in_seconds: int


class WorkflowTaskResponse(BaseModel):
    id: UUID
    workflow_run_id: UUID
    task_key: str
    task_type: str
    assigned_agent: str
    status: str
    priority: int
    input_refs: dict
    output_refs: dict
    attempt_count: int
    max_attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}
