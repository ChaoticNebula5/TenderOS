import uuid

from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState


async def specialist_join_node(state: TenderState) -> dict:
    """The seam R2's fixed topology converges on after specialist_dispatch's
    parallel fan-out. By the time LangGraph calls this node, every Send()'d
    specialist branch has already merged its completion id into
    completed_task_ids via the operator.add reducer (state.py's Annotated field)
    - LangGraph's own execution model guarantees all fanned-out branches finish
    before a shared downstream node runs, so there's no manual waiting/polling
    here. This is also the insertion point later blocks (B9-B12) extend: their
    real subgraphs slot in between this node and final_gate without this node
    itself changing.
    """
    await sync_run_status(uuid.UUID(state["workflow_run_id"]), "awaiting_final_approval")
    return {"status": "awaiting_final_approval"}
