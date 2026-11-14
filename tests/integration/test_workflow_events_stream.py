"""routes/workflow.py's _workflow_progress_stream (B14 SSE) against real
Postgres - direct, generator-level testing, not through HTTP.

httpx.ASGITransport cannot test this route's full HTTP delivery at all:
confirmed directly (a minimal FastAPI + StreamingResponse + infinite
generator repro, run outside this test file, hung identically) that
ASGITransport.handle_async_request does not return control to the client
until the whole ASGI app() call completes - Starlette's own
StreamingResponse runs a concurrent listen_for_disconnect task awaiting
receive() alongside the body generator, and ASGITransport's mock receive()
only resolves on a real disconnect signal that never arrives for an
in-process test with no real client to send one. This is a structural
limitation of the test harness against an unbounded generator, not a bug
in the route - test_workflow_api.py's own
test_stream_workflow_events_rejects_a_caller_outside_the_organization
covers what CAN be verified over real HTTP (the auth/RBAC dependency
chain, which rejects before the generator ever starts). This file is the
real, direct proof the generator itself is correct."""

import json
import uuid

import pytest
from tenderos_api.routes.workflow import _workflow_progress_stream
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork

pytestmark = pytest.mark.asyncio


async def _make_tender(name: str) -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        return tender.id


async def test_stream_emits_a_heartbeat_then_a_real_task_created_event():
    tender_id = await _make_tender("SSE Stream Test")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()

    gen = await _workflow_progress_stream(run.id)
    first_chunk = await anext(gen)
    assert first_chunk == ": heartbeat\n\n", (
        "nothing has happened yet - must not fabricate an event"
    )

    async with UnitOfWork() as uow:
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key="requirement_extraction",
            task_type="requirement_extraction",
            assigned_agent="requirements",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    data_chunk = None
    async for chunk in gen:
        if chunk.startswith("data:"):
            data_chunk = chunk
            break
    assert data_chunk is not None
    message = json.loads(data_chunk.removeprefix("data:").strip())
    assert message["event_type"] == "task.created"
    assert message["workflow_run_id"] == str(run.id)
    assert message["task_id"] == str(task.id)
    assert message["task_type"] == "requirement_extraction"
    assert "timestamp" in message


async def test_stream_delivers_task_started_when_a_task_begins():
    """Phase 4 plan item 77: the open page re-reads its tasks on any stream
    message, so this event is what makes a running task read Running there
    without a reload."""
    tender_id = await _make_tender("SSE Task Started Test")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        task = await uow.workflow.create_task(
            workflow_run_id=run.id,
            task_key="talent_analysis",
            task_type="talent_analysis",
            assigned_agent="talent",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    # Opened after the task.created event: the stream's starting set skips
    # what was already there (plan item 54), so the next message is ours.
    gen = await _workflow_progress_stream(run.id)
    assert await anext(gen) == ": heartbeat\n\n"

    async with UnitOfWork() as uow:
        assert await uow.workflow.mark_task_running(task.id)
        await uow.commit()

    # Bounded: the stream heartbeats while nothing matches, so an event the
    # stream never sends must fail this test, not hang it.
    data_chunk = None
    for _ in range(20):
        chunk = await anext(gen)
        if chunk.startswith("data:"):
            data_chunk = chunk
            break
    assert data_chunk is not None, "no task.started message within 20 stream chunks"
    message = json.loads(data_chunk.removeprefix("data:").strip())
    assert message["event_type"] == "task.started"
    assert message["workflow_run_id"] == str(run.id)
    assert message["task_id"] == str(task.id)
    assert message["task_type"] == "talent_analysis"


async def test_stream_ignores_events_from_a_different_run():
    tender_id = await _make_tender("SSE Cross-Run Isolation Test")
    # One active run per tender (plan item 43): the other run is finished
    # before the watched one starts; its task event still lands on the
    # tender's same stream query, which must ignore it by run id.
    async with UnitOfWork() as uow:
        other_run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.workflow.update_run_status(other_run.id, "completed")
        watched_run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.commit()

    gen = await _workflow_progress_stream(watched_run.id)
    first_chunk = await anext(gen)
    assert first_chunk == ": heartbeat\n\n"

    async with UnitOfWork() as uow:
        await uow.workflow.create_task(
            workflow_run_id=other_run.id,
            task_key="requirement_extraction",
            task_type="requirement_extraction",
            assigned_agent="requirements",
            priority=10,
            input_refs={},
        )
        await uow.commit()

    # The watched run has had no real event - every further chunk this
    # generator produces while polling must be a heartbeat, never a data
    # line describing the other run's task.
    for _ in range(3):
        chunk = await anext(gen)
        assert chunk == ": heartbeat\n\n", f"leaked another run's event: {chunk!r}"
