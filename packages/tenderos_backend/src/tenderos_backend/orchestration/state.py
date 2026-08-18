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

    # B11: extends R3's own locked schema the same way SpecialistTaskState
    # already extends it with task_id - an implementation-necessary field
    # R3's own list doesn't name. Snapshotted by targeted_replan_node from
    # open_critical_finding_ids immediately before it dispatches a
    # revision, so route_after_red_team can compare "open critical findings
    # now" against "open critical findings going into the last revision" on
    # the following pass - the real progress check B2.4 requires ("no
    # measurable progress" as its own stop condition, separate from budget).
    pre_revision_finding_ids: NotRequired[list[str]]
    # B11: set by targeted_replan_node, read once by route_after_targeted_
    # replan. Disambiguates two states that would otherwise both look like
    # "dispatch_task_ids is empty": nothing was auto-correctable at all
    # ("none" -> finding_gate) vs. the only corrective task needed is a
    # proposal redraft, which never goes through specialist_dispatch's
    # Send() mechanism at all ("proposal_drafting" -> straight there).
    # "specialist_dispatch" is the third value, used whenever at least one
    # compliance_analysis task was actually created.
    revision_dispatch_target: NotRequired[str]

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
