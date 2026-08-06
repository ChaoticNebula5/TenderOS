import uuid

from tenderos_backend.domain.services.workflow_service import sync_run_status
from tenderos_backend.orchestration.state import TenderState


async def load_context_node(state: TenderState) -> dict:
    """First node after START (R2's fixed topology).

    No real document/requirement corpus exists yet in B3 - there's nothing to
    load besides advancing status so the supervisor node has a real "planning"
    transition to build its stub plan against.
    """
    await sync_run_status(uuid.UUID(state["workflow_run_id"]), "planning")
    return {"status": "planning"}
