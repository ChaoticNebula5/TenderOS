import logging
import uuid

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import is_cancelled, sync_run_status
from tenderos_backend.orchestration.checkpoint import get_checkpointer
from tenderos_backend.orchestration.graph import build_graph
from tenderos_backend.orchestration.state import TenderState

logger = logging.getLogger(__name__)


async def run_workflow(workflow_run_id: str) -> None:
    """Enqueued on WorkflowRunCreated (see outbox_dispatcher.py's routing table).

    Builds the graph's initial TenderState from the workflow_run + tender rows
    and invokes it fresh - runs until the graph either completes or pauses at
    the first interrupt() (requirement_gate).
    """
    run_id = uuid.UUID(workflow_run_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        if run is None:
            raise ValueError(f"WorkflowRun {run_id} not found")
        tender = await uow.tenders.get(run.tender_id)
        if tender is None:
            raise ValueError(f"Tender {run.tender_id} not found")

    if run.status == "cancelled":
        # I6: both queue entry points are the only places that ever call
        # graph.ainvoke() - checking here means a cancelled run never even
        # starts, no LangGraph execution at all.
        logger.info("workflow run %s is cancelled, skipping", run_id)
        return

    initial_state: TenderState = {
        "tender_id": str(tender.id),
        "organization_id": str(tender.organization_id),
        "workflow_run_id": str(run.id),
        "langgraph_thread_id": run.langgraph_thread_id,
        "status": "created",
        "plan_version": 0,
        "active_gate_id": None,
        "revision_count": 0,
        "max_revisions": 2,
        "retry_budget_remaining": 10,
        "dispatch_task_ids": [],
        "completed_task_ids": [],
        "failed_task_ids": [],
        "unverified_requirement_ids": [],
        "open_critical_finding_ids": [],
        "changed_knowledge_item_ids": [],
        "readiness_report_id": None,
        "graph_projection_version": None,
        "degraded_stages": [],
    }
    config: RunnableConfig = {"configurable": {"thread_id": run.langgraph_thread_id}}

    logger.info("starting workflow run %s", run_id)
    try:
        async with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)
            await graph.ainvoke(initial_state, config)
        logger.info("workflow run %s paused or completed", run_id)
    except Exception:
        # Individual specialist nodes mark their own WorkflowTask failed
        # before re-raising (see orchestration/nodes/compliance.py etc.),
        # but nothing upstream of graph.ainvoke() previously caught that
        # re-raise to move the RUN to a terminal status - an exception here
        # left workflow_runs.status stuck at whatever non-terminal value it
        # last had, forever (see KNOWN_LIMITATIONS.md's B0-B11 catch-up
        # review). ingestion.py's parse_and_embed_document is the one job
        # in this codebase that already does this correctly; mirrored here.
        logger.exception("workflow run %s failed", run_id)
        # The status==cancelled check above only covers the moment BEFORE
        # ainvoke() started - a cancellation recorded while a node is
        # mid-flight lands here too when that node's own work then raises.
        # Re-checking here, not just before, is what stops "failed" from
        # silently overwriting a real, deliberate cancellation.
        if not await is_cancelled(run_id):
            await sync_run_status(run_id, "failed")
        raise


async def resume_workflow(workflow_run_id: str, gate_id: str) -> None:
    """Enqueued on GateDecisionRecorded (see outbox_dispatcher.py's routing
    table). Resumes the exact paused thread with the recorded decision -
    NOT a fresh invocation (R1: "resume the exact checkpointed thread").
    """
    run_id = uuid.UUID(workflow_run_id)
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        gate = await uow.gates.get(uuid.UUID(gate_id))
    if run is None:
        raise ValueError(f"WorkflowRun {run_id} not found")
    if gate is None or gate.decision_payload is None:
        raise ValueError(f"ApprovalGate {gate_id} has no recorded decision")

    if run.status == "cancelled":
        logger.info("workflow run %s is cancelled, skipping resume", run_id)
        return

    config: RunnableConfig = {"configurable": {"thread_id": run.langgraph_thread_id}}

    resume_command: Command[str] = Command(resume=gate.decision_payload)

    try:
        async with get_checkpointer() as checkpointer:
            graph = build_graph(checkpointer)

            await graph.ainvoke(resume_command, config)
    except Exception:
        # See run_workflow's identical catch above - same gap, same fix,
        # including the re-check of cancellation-during-execution.
        logger.exception("workflow run %s failed while resuming", run_id)
        if not await is_cancelled(run_id):
            await sync_run_status(run_id, "failed")
        raise
