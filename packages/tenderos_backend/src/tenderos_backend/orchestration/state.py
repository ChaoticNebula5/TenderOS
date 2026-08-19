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

    # proposal_drafting_node/project_graph_node have no workflow_tasks row
    # (they're plain sequential nodes, not Send()-dispatched - see their own
    # docstrings) and deliberately swallow their own exceptions so the run
    # can still reach final_gate rather than dying mid-pipeline. Without
    # this, that swallow was silent: the run could report "completed" with
    # zero proposal sections drafted or a provably-stale graph, and nothing
    # in run/task state said so. Same accumulate-don't-overwrite reducer as
    # completed_task_ids/failed_task_ids above - a later stage's success
    # must never erase an earlier stage's failure from this list. Read by
    # final_gate_node when building its gate summary (persisted into
    # approval_gates.request_payload, so it's visible to the human deciding
    # the gate and to any later reader of that row, not just this run's own
    # transient LangGraph state).
    degraded_stages: Annotated[list[str], operator.add]


class SpecialistTaskState(TenderState):
    # specialist_dispatch's Send(node_name, payload) hands each of the 3
    # parallel specialist nodes a payload with this one extra field beyond the
    # shared TenderState - TypedDict subclassing (not a separate unrelated
    # type) so the specialist nodes still get every ordinary TenderState field
    # typed correctly, plus this one.
    task_id: str
