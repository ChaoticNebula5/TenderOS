import uuid

from langgraph.types import Send

from tenderos_backend.domain.services.workflow_service import is_cancelled
from tenderos_backend.orchestration.state import TenderState

# Fixed order matching orchestration.supervisor.build_plan's _STUB_TASK_PLAN -
# supervisor_node built dispatch_task_ids in this same order, so
# dispatch_task_ids[i] is the WorkflowTask id for _SPECIALIST_NODES[i]'s task.
# These strings are also the exact node names graph.py registers the
# compliance/talent/commercial nodes under via add_node(...). "compliance_stub"
# became "compliance_analysis" in B7, "talent_stub" became "talent_analysis"
# in B8A, and "commercial_stub" became "commercial_analysis" in B8B - all
# three specialist branches are now real.
_SPECIALIST_NODES = ("compliance_analysis", "talent_analysis", "commercial_analysis")


async def dispatch_specialists(state: TenderState) -> list[Send]:
    # I6: this is the literal "prevents new tasks" enforcement point - the
    # actual moment new workflow_tasks get Send()'d to run. Returning an
    # empty list means no specialist branches run and specialist_join's
    # incoming edges never fire, so the graph simply stops making progress
    # here rather than erroring - workflow_runs.status already reads
    # "cancelled" (set by cancel_workflow_run), untouched by this branch.
    if await is_cancelled(uuid.UUID(state["workflow_run_id"])):
        return []

    task_ids = state["dispatch_task_ids"]
    sends: list[Send] = []

    for node_name, task_id in zip(_SPECIALIST_NODES, task_ids, strict=True):
        payload = {**state, "task_id": task_id}
        sends.append(Send(node_name, payload))

    return sends
