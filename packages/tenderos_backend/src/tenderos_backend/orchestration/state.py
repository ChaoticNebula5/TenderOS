from __future__ import annotations

import operator
from typing import Annotated, NotRequired, TypedDict

from tenderos_backend.core.enums import WorkflowStatus

__all__ = ["SpecialistTaskState", "TenderState", "WorkflowStatus"]


class TenderState(TypedDict):
    tender_id: str
    organization_id: str
    workflow_run_id: str
    langgraph_thread_id: str

    status: WorkflowStatus
    plan_version: int
    active_gate_id: str | None

    revision_count: int
    max_revisions: int
    retry_budget_remaining: int

    dispatch_task_ids: list[str]
    completed_task_ids: Annotated[list[str], operator.add]
    failed_task_ids: Annotated[list[str], operator.add]

    unverified_requirement_ids: list[str]
    open_critical_finding_ids: list[str]
    changed_knowledge_item_ids: list[str]

    readiness_report_id: str | None
    graph_projection_version: int | None

    last_error_code: NotRequired[str | None]


class SpecialistTaskState(TenderState):
    # specialist_dispatch's Send(node_name, payload) hands each of the 3
    # parallel specialist nodes a payload with this one extra field beyond the
    # shared TenderState - TypedDict subclassing (not a separate unrelated
    # type) so the specialist nodes still get every ordinary TenderState field
    # typed correctly, plus this one.
    task_id: str
