"""Phase 4 plan item 44 (KNOWN_LIMITATIONS, "Cancellation-during-execution
race, remaining half"): a cancelled run stays cancelled.

A node still running in a cancelled run's job used to write a non-terminal
status over "cancelled" and revive the run, or, since item 43, fail on the
one-active-run index when a newer run was active: either way with a
traceback and a failed RQ job. Now every status write refuses to leave
completed, failed or cancelled (one conditional UPDATE), a gate opens only
for an active run (checked in the gate's own transaction), cancelling
closes the run's pending gates, and the job stops quietly.

The job-level tests run the real run_workflow with the graph replaced by a
one-step graph whose step does what a node does when a cancel lands
mid-flight: the cancel commits, then the step writes its status or opens a
gate.
"""

import logging
import uuid

import pytest
from _helpers import register_org_and_tender
from langgraph.graph import END, START, StateGraph
from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import ApprovalGate
from tenderos_backend.db.repositories.gates import CLOSED_BY_CANCEL_PAYLOAD
from tenderos_backend.db.repositories.workflow import RunNoLongerActiveError
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_WORKFLOW_RUN_STATUS_CHANGED
from tenderos_backend.domain.services import gate_service, workflow_service
from tenderos_backend.orchestration.gates import build_gate_payload, derive_gate_key
from tenderos_backend.orchestration.nodes import requirement as requirement_node
from tenderos_backend.orchestration.state import TenderState
from tenderos_worker.jobs import workflow as workflow_jobs
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio

_JOB_LOGGER = "tenderos_worker.jobs.workflow"


async def _tender(label: str):
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    return await register_org_and_tender(email, f"{email} org", f"{email} tender")


async def _queued_run(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()
        return run.id


async def _status(run_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        return (await uow.workflow.get_run(run_id)).status


async def _status_events(run_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        return (
            await uow.session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == run_id,
                    OutboxEvent.event_type == EVENT_WORKFLOW_RUN_STATUS_CHANGED,
                )
            )
        ).scalar_one()


async def _gates(run_id: uuid.UUID) -> list[ApprovalGate]:
    async with UnitOfWork() as uow:
        return await uow.gates.list_for_run(run_id)


def _scheduled_retries(run_id: uuid.UUID) -> int:
    connection = Redis.from_url(settings.redis_url)
    try:
        queue = Queue("workflow", connection=connection)
        jobs = [queue.fetch_job(job_id) for job_id in queue.scheduled_job_registry.get_job_ids()]
        return sum(1 for job in jobs if job is not None and job.args == (str(run_id),))
    finally:
        connection.close()


def _one_step_graph(step):
    def build(checkpointer):
        graph = StateGraph(TenderState)
        graph.add_node("step", step)
        graph.add_edge(START, "step")
        graph.add_edge("step", END)
        return graph.compile(checkpointer=checkpointer)

    return build


def _assert_stopped_quietly(caplog, run_id: uuid.UUID) -> None:
    ours = [r for r in caplog.records if r.name == _JOB_LOGGER]
    stopped = [r for r in ours if "no longer active" in r.getMessage()]
    assert len(stopped) == 1 and stopped[0].levelno == logging.INFO, [r.getMessage() for r in ours]
    assert str(run_id) in stopped[0].getMessage()
    assert not [r for r in caplog.records if r.exc_info or r.levelno >= logging.WARNING], [
        (r.name, r.levelname, r.getMessage())
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ]
    assert _scheduled_retries(run_id) == 0


@pytest.mark.parametrize("newer_run", [False, True], ids=["no newer run", "newer run active"])
async def test_a_cancelled_runs_job_writing_drafting_leaves_it_cancelled(
    monkeypatch, caplog, newer_run
):
    registered, tender = await _tender("cas-drafting")
    run_id = await _queued_run(tender.id)
    newer: list[uuid.UUID] = []

    async def cancel_then_write(state: TenderState) -> dict:
        await workflow_service.cancel_workflow_run(
            run_id, registered.organization.id, registered.user.id
        )
        if newer_run:
            newer.append(await _queued_run(tender.id))
        await workflow_service.sync_run_status(run_id, "drafting")
        return {}

    monkeypatch.setattr(workflow_jobs, "build_graph", _one_step_graph(cancel_then_write))
    caplog.set_level(logging.INFO)

    await workflow_jobs.run_workflow(str(run_id))

    assert await _status(run_id) == "cancelled"
    # running (the claim) and cancelled; no third, reviving event.
    assert await _status_events(run_id) == 2
    if newer_run:
        assert await _status(newer[0]) == "queued"
    _assert_stopped_quietly(caplog, run_id)


async def test_a_gate_step_on_a_cancelled_run_opens_no_gate(monkeypatch, caplog):
    """The real requirement gate node. The cancel lands just after its
    entry check (is_cancelled returns False, as it did a moment earlier),
    so only the check in the gate's own transaction can stop it."""
    registered, tender = await _tender("cas-gate")
    run_id = await _queued_run(tender.id)

    async def cancel_after_the_entry_check(_run_id: uuid.UUID) -> bool:
        await workflow_service.cancel_workflow_run(
            run_id, registered.organization.id, registered.user.id
        )
        return False

    monkeypatch.setattr(requirement_node, "is_cancelled", cancel_after_the_entry_check)
    monkeypatch.setattr(
        workflow_jobs, "build_graph", _one_step_graph(requirement_node.requirement_gate_node)
    )
    caplog.set_level(logging.INFO)

    await workflow_jobs.run_workflow(str(run_id))

    assert await _status(run_id) == "cancelled"
    assert await _gates(run_id) == []
    _assert_stopped_quietly(caplog, run_id)


async def test_a_gate_is_refused_for_a_run_that_is_not_active():
    _registered, tender = await _tender("cas-gate-direct")
    run_id = await _queued_run(tender.id)
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run_id, "completed")
        await uow.commit()

    async with UnitOfWork() as uow:
        gate_id = uuid.uuid4()
        with pytest.raises(RunNoLongerActiveError):
            await uow.gates.create(
                gate_id=gate_id,
                tender_id=tender.id,
                workflow_run_id=run_id,
                gate_key=derive_gate_key("requirement_review", 0),
                gate_type="requirement_review",
                request_payload=build_gate_payload(
                    gate_id, "requirement_review", run_id, targets=[], summary={}
                ),
            )

    assert await _gates(run_id) == []


async def test_cancelling_closes_a_pending_gate():
    registered, tender = await _tender("cas-close")
    run_id = await _queued_run(tender.id)
    gate_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        await uow.gates.create(
            gate_id=gate_id,
            tender_id=tender.id,
            workflow_run_id=run_id,
            gate_key=derive_gate_key("requirement_review", 0),
            gate_type="requirement_review",
            request_payload=build_gate_payload(
                gate_id, "requirement_review", run_id, targets=[], summary={}
            ),
        )
        await uow.commit()

    await workflow_service.cancel_workflow_run(
        run_id, registered.organization.id, registered.user.id
    )

    [gate] = await _gates(run_id)
    assert gate.status == "rejected"
    assert gate.decision_payload == CLOSED_BY_CANCEL_PAYLOAD
    assert gate.decided_at is not None
    # A later decision is refused, never taken for a replay.
    for decision in ("approve", "reject"):
        with pytest.raises(gate_service.GateAlreadyDecidedError):
            await gate_service.record_decision(
                gate_id=gate_id,
                organization_id=registered.organization.id,
                decided_by=registered.user.id,
                decision=decision,
                notes=None,
            )
