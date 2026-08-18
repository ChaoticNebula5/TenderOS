import uuid

from langgraph.types import Send

from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import is_cancelled
from tenderos_backend.orchestration.state import TenderState

# task_type -> the exact node name graph.py registers it under via
# add_node(...). B3's original design zipped a fixed 3-tuple against
# dispatch_task_ids positionally, always sending all three - correct for
# B3's own always-all-3 stub plan, but wrong for B11's revision passes,
# where targeted_replan_node may dispatch only ONE task_type (e.g. just
# compliance_analysis) and re-running the other two would re-litigate
# already-approved, unrelated decisions - the opposite of "targeted." This
# dict-based lookup is a strict generalization: B3's stub still produces
# all 3 task_types every first pass, so first-pass behavior is byte-for-
# byte unchanged; only a revision pass will ever Send() fewer than 3.
_TASK_TYPE_TO_NODE = {
    "compliance_analysis": "compliance_analysis",
    "talent_analysis": "talent_analysis",
    "commercial_validation": "commercial_analysis",
}


async def dispatch_specialists(state: TenderState) -> list[Send]:
    # I6: this is the literal "prevents new tasks" enforcement point - the
    # actual moment new workflow_tasks get Send()'d to run. Returning an
    # empty list means no specialist branches run and specialist_join's
    # incoming edges never fire, so the graph simply stops making progress
    # here rather than erroring - workflow_runs.status already reads
    # "cancelled" (set by cancel_workflow_run), untouched by this branch.
    if await is_cancelled(uuid.UUID(state["workflow_run_id"])):
        return []

    task_ids = {uuid.UUID(t) for t in state["dispatch_task_ids"]}
    async with UnitOfWork() as uow:
        tasks = await uow.workflow.list_tasks(uuid.UUID(state["workflow_run_id"]))

    sends: list[Send] = []
    for task in tasks:
        if task.id not in task_ids:
            continue
        node_name = _TASK_TYPE_TO_NODE.get(task.task_type)
        if node_name is None:
            continue
        payload = {**state, "task_id": str(task.id)}
        sends.append(Send(node_name, payload))

    return sends
