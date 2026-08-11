import pytest
from _helpers import make_ready_run_inputs
from redis import Redis
from rq import Queue
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import (
    auth_service,
    tender_service,
    workflow_service,
)
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, _dispatch_once
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio


async def test_dispatcher_marks_event_dispatched_on_success():
    """Depends on outbox_dispatcher's retry/backoff TODO being implemented -
    currently raises NotImplementedError on every pending event."""
    registered = await auth_service.register(
        email="dispatch@example.com", password="hunter2", organization_name="Dispatch Org"
    )
    tender = await tender_service.create_tender(
        organization_id=registered.organization.id,
        name="Dispatch Tender",
        created_by=registered.user.id,
    )

    await _dispatch_once()

    async with UnitOfWork() as uow:
        still_pending = await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)

    assert all(e.aggregate_id != tender.id for e in still_pending)


async def test_workflow_run_created_routes_to_the_workflow_queue():
    """The routing table, not just generic dispatch success - WorkflowRunCreated
    must land on the "workflow" queue running run_workflow, not "maintenance"
    running the generic process_outbox_event placeholder every other event
    type still falls back to."""
    registered, tender = await make_ready_run_inputs(
        "dispatch-routing@example.com", "Routing Org", "Routing Tender"
    )
    run = await workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )

    await _dispatch_once()

    connection = Redis.from_url(settings.redis_url)
    workflow_queue = Queue("workflow", connection=connection)
    assert f"workflow-{run.id}" in workflow_queue.job_ids
    job = workflow_queue.fetch_job(f"workflow-{run.id}")
    assert job is not None
    assert job.func_name.endswith("run_workflow")
