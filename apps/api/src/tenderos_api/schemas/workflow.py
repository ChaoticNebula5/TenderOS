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
