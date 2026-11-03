"""Phase 4: async operations (operation_service, AsyncOperationRepository,
the worker's run_operation) against real Postgres.

The outbox delivers OperationRequested at least once, so run_operation can
be called twice for one operation, one after the other or at the same
moment. Only the call that moves the operation from 'queued' to 'running'
may do the work, and only the holder of that claim may finish it. Most
tests here replace the kind's handler with a stand-in that counts calls:
the question is how many times the work would run, not what it does. The
last tests run the real work for each kind (FakeLLMClient; real Neo4j for
the red team, real object storage for commercial validation) end to end.
"""

import asyncio
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from _helpers import register_org_and_tender
from redis import Redis
from rq import Queue
from sqlalchemy import func, select, update
from tenderos_backend.agents.llm.client import LLMTimeoutError
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.schemas.commercial import FindingExplanation
from tenderos_backend.agents.schemas.proposal import ProposalSectionDraft
from tenderos_backend.agents.schemas.redteam import RedteamCritique
from tenderos_backend.db.models.async_operation import OPERATION_KINDS, AsyncOperation
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.async_operations import (
    DISPATCH_FAILED,
    WORKER_LOST,
    AsyncOperationRepository,
)
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    EVENT_OPERATION_REQUESTED,
    EVENT_OPERATION_STATUS_CHANGED,
)
from tenderos_backend.domain.services import (
    commercial_service,
    document_service,
    operation_service,
    proposal_service,
)
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_worker.jobs import operations as operation_jobs
from tenderos_worker.jobs.operations import run_operation, run_operation_job
from tenderos_worker.outbox_dispatcher import operation_job_id, operation_job_known
from tenderos_worker.settings import settings
from tenderos_worker.worker import OPERATIONS_QUEUE

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10
_PRICING_FIXTURES = (
    Path(__file__).resolve().parent.parent.parent / "data" / "synthetic" / "tender_a"
)


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    # The real red-team run (last tests) reads the graph; same cleanup as
    # test_redteam_service.py.
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


class _CountingHandler:
    def __init__(self) -> None:
        self.calls: list[uuid.UUID] = []

    async def __call__(self, operation: AsyncOperation) -> None:
        self.calls.append(operation.id)


def _replace_redteam_handler(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    monkeypatch.setitem(operation_jobs._HANDLERS, "redteam_run", handler)


async def _request(email: str, kind: str = "redteam_run") -> operation_service.OperationRequest:
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    return await operation_service.request_operation(
        registered.organization.id, tender.id, kind, registered.user.id
    )


async def _reload(operation_id: uuid.UUID) -> AsyncOperation:
    operation = await operation_service.get_operation(operation_id)
    assert operation is not None
    return operation


async def _statuses_announced(operation_id: uuid.UUID) -> list[str]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == operation_id,
                OutboxEvent.event_type == EVENT_OPERATION_STATUS_CHANGED,
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
        )
        return [e.payload["status"] for e in result.scalars().all()]


async def test_a_request_records_a_queued_operation_and_its_events():
    requested = await _request("op-request@example.com")

    assert requested.created is True
    assert requested.operation.status == "queued"
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == requested.operation.id,
                OutboxEvent.event_type == EVENT_OPERATION_REQUESTED,
            )
        )
        events = list(result.scalars().all())
    assert len(events) == 1
    assert events[0].payload == {
        "operation_id": str(requested.operation.id),
        "kind": "redteam_run",
    }
    assert await _statuses_announced(requested.operation.id) == ["queued"]


async def test_a_second_request_while_active_returns_the_same_operation():
    first = await _request("op-second@example.com")
    op = first.operation

    again = await operation_service.request_operation(
        op.organization_id, op.tender_id, "redteam_run", op.requested_by
    )
    assert again.created is False
    assert again.operation.id == op.id

    # Still the same one once it is running.
    claimed = await operation_service.claim_operation(op.id, operation_service.lease_for(op.kind))
    assert claimed is not None and claimed.claim_token is not None
    while_running = await operation_service.request_operation(
        op.organization_id, op.tender_id, "redteam_run", op.requested_by
    )
    assert while_running.operation.id == op.id

    # Finished: the next request is a new operation.
    assert await operation_service.finish_operation(op.id, claimed.claim_token, "succeeded")
    after = await operation_service.request_operation(
        op.organization_id, op.tender_id, "redteam_run", op.requested_by
    )
    assert after.created is True
    assert after.operation.id != op.id


async def test_a_duplicate_delivery_does_not_run_the_work_twice(monkeypatch):
    handler = _CountingHandler()
    _replace_redteam_handler(monkeypatch, handler)
    requested = await _request("op-dup@example.com")
    op_id = requested.operation.id

    await run_operation(str(op_id))
    await run_operation(str(op_id))

    assert handler.calls == [op_id]
    finished = await _reload(op_id)
    assert finished.status == "succeeded"
    assert finished.started_at is not None and finished.finished_at is not None
    assert await _statuses_announced(op_id) == ["queued", "running", "succeeded"]


@pytest.mark.parametrize("kind", OPERATION_KINDS)
async def test_a_duplicate_delivery_runs_each_kind_once(monkeypatch, kind):
    """Stage 2: the same claim guards every kind, so each one's handler runs
    once under a duplicate delivery, and every kind has a handler."""
    handler = _CountingHandler()
    monkeypatch.setitem(operation_jobs._HANDLERS, kind, handler)
    requested = await _request(f"op-dup-{kind}@example.com", kind)
    op_id = requested.operation.id

    await run_operation(str(op_id))
    await run_operation(str(op_id))

    assert handler.calls == [op_id]
    assert (await _reload(op_id)).status == "succeeded"
    assert await _statuses_announced(op_id) == ["queued", "running", "succeeded"]


async def test_two_concurrent_deliveries_claim_exactly_once(monkeypatch):
    """A barrier holds both deliveries until each is about to run its claim,
    so the two conditional UPDATEs really are concurrent (the same shape as
    test_run_workflow_claim.py). Postgres makes the second wait on the row
    lock, then re-checks status = 'queued' against the row the first wrote."""
    handler = _CountingHandler()
    _replace_redteam_handler(monkeypatch, handler)
    requested = await _request("op-race@example.com")
    op_id = requested.operation.id

    barrier = asyncio.Barrier(2)
    original_claim = AsyncOperationRepository.claim
    claims: list[bool] = []

    async def claim_together(self, operation_id, lease):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        claimed = await original_claim(self, operation_id, lease)
        claims.append(claimed is not None)
        return claimed

    monkeypatch.setattr(AsyncOperationRepository, "claim", claim_together)

    await asyncio.wait_for(
        asyncio.gather(run_operation(str(op_id)), run_operation(str(op_id))),
        timeout=_WAIT_SECONDS,
    )

    assert sorted(claims) == [False, True]
    assert handler.calls == [op_id]
    assert (await _reload(op_id)).status == "succeeded"


async def test_the_claim_sets_a_lease_of_job_timeout_plus_margin():
    requested = await _request("op-lease@example.com")
    op_id = requested.operation.id

    claimed = await operation_service.claim_operation(
        op_id, operation_service.lease_for("redteam_run")
    )

    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.claim_token is not None
    assert claimed.started_at is not None and claimed.locked_until is not None
    # Both are the database clock's now() in one statement, so the lease is
    # exact: 600 s job timeout + 60 s margin.
    assert (claimed.locked_until - claimed.started_at).total_seconds() == 660


async def test_finish_needs_the_claim_token_and_a_running_operation():
    requested = await _request("op-finish@example.com")
    op_id = requested.operation.id

    # Not claimed yet: nothing to finish.
    assert not await operation_service.finish_operation(op_id, uuid.uuid4(), "succeeded")
    assert (await _reload(op_id)).status == "queued"

    claimed = await operation_service.claim_operation(
        op_id, operation_service.lease_for("redteam_run")
    )
    assert claimed is not None and claimed.claim_token is not None

    # Someone else's token: refused, nothing written.
    assert not await operation_service.finish_operation(op_id, uuid.uuid4(), "succeeded")
    assert (await _reload(op_id)).status == "running"

    assert await operation_service.finish_operation(
        op_id, claimed.claim_token, "failed", "LLM_TIMEOUT", "fake message"
    )
    # Already finished: the same token cannot finish it again.
    assert not await operation_service.finish_operation(op_id, claimed.claim_token, "succeeded")

    final = await _reload(op_id)
    assert (final.status, final.error_code, final.error_message) == (
        "failed",
        "LLM_TIMEOUT",
        "fake message",
    )
    assert await _statuses_announced(op_id) == ["queued", "running", "failed"]


async def test_the_reaper_fails_a_lapsed_operation_and_leaves_a_live_one_alone():
    lapsed_req = await _request("op-reap-lapsed@example.com")
    live_req = await _request("op-reap-live@example.com")
    lapsed_id, live_id = lapsed_req.operation.id, live_req.operation.id
    lease = operation_service.lease_for("redteam_run")
    lapsed_claim = await operation_service.claim_operation(lapsed_id, lease)
    live_claim = await operation_service.claim_operation(live_id, lease)
    assert lapsed_claim is not None and live_claim is not None
    assert lapsed_claim.claim_token is not None

    # The worker holding lapsed_id died: its lease ran out a minute ago.
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id == lapsed_id)
            .values(locked_until=AsyncOperation.started_at - operation_service.LEASE_MARGIN)
        )
        await uow.commit()

    reaped = await operation_service.reap_lapsed_operations(batch_size=100)

    assert reaped == [lapsed_id]
    failed = await _reload(lapsed_id)
    assert failed.status == "failed"
    assert failed.error_code == WORKER_LOST
    assert failed.finished_at is not None
    assert await _statuses_announced(lapsed_id) == ["queued", "running", "failed"]
    assert (await _reload(live_id)).status == "running"

    # Reaped, never taken over: the lost worker's late finish is refused.
    assert not await operation_service.finish_operation(
        lapsed_id, lapsed_claim.claim_token, "succeeded"
    )
    assert (await _reload(lapsed_id)).status == "failed"
    # A second pass finds nothing more.
    assert await operation_service.reap_lapsed_operations(batch_size=100) == []


# --- the queued reapers: lost jobs, and the absolute cap (plan item 44) ------
#
# Real RQ on the test Redis: the operations queue has no worker in the test
# process, so a job enqueued here stays queued, as one waiting behind another
# tenant's long operation does. The check is the dispatcher's own
# (outbox_dispatcher.operation_job_known).


@pytest.fixture
def operations_queue():
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(OPERATIONS_QUEUE, connection=connection)
    enqueued: list[str] = []

    def enqueue(operation_id: uuid.UUID) -> None:
        job_id = operation_job_id(operation_id)
        queue.enqueue(run_operation_job, str(operation_id), job_id=job_id)
        enqueued.append(job_id)

    yield connection, enqueue
    for job_id in enqueued:
        job = queue.fetch_job(job_id)
        if job is not None:
            job.delete()
    connection.close()


async def _mark_dispatched(operation_id: uuid.UUID) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == operation_id,
                OutboxEvent.event_type == EVENT_OPERATION_REQUESTED,
            )
            .values(status="dispatched")
        )
        await uow.commit()


async def _requested_ago(operation_id: uuid.UUID, age: timedelta) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(AsyncOperation)
            .where(AsyncOperation.id == operation_id)
            .values(created_at=func.now() - age)
        )
        await uow.commit()


async def test_an_operation_waiting_behind_another_long_job_is_not_failed(operations_queue):
    """Queued two hours (past the old 60-minute window), its job still in
    RQ's queue behind another tenant's: it is waiting, not lost."""
    connection, enqueue = operations_queue
    ahead = await _request("op-reap-ahead@example.com", kind="proposal_generation")
    waiting = await _request("op-reap-waiting@example.com")
    for requested in (ahead, waiting):
        await _mark_dispatched(requested.operation.id)
        enqueue(requested.operation.id)
    await _requested_ago(waiting.operation.id, timedelta(hours=2))

    lost = await operation_service.reap_lost_queued_operations(100, operation_job_known(connection))
    capped = await operation_service.reap_queued_past_cap(batch_size=100)

    assert lost == [] and capped == []
    assert (await _reload(waiting.operation.id)).status == "queued"
    assert await _statuses_announced(waiting.operation.id) == ["queued"]


async def test_an_operation_whose_rq_job_is_gone_is_failed(operations_queue):
    """Dispatched, but RQ holds no job for it any more (lost between enqueue
    and start): failed as DISPATCH_FAILED at once, however recent. One whose
    request is still pending has had no job yet (the outbox may be
    retrying) and is left alone."""
    connection, enqueue = operations_queue
    gone = await _request("op-reap-gone@example.com")
    pending = await _request("op-reap-pending@example.com")
    await _mark_dispatched(gone.operation.id)
    enqueue(gone.operation.id)
    Queue(OPERATIONS_QUEUE, connection=connection).fetch_job(
        operation_job_id(gone.operation.id)
    ).delete()

    reaped = await operation_service.reap_lost_queued_operations(
        100, operation_job_known(connection)
    )

    assert reaped == [gone.operation.id]
    failed = await _reload(gone.operation.id)
    assert failed.status == "failed"
    assert failed.error_code == DISPATCH_FAILED
    assert failed.finished_at is not None
    assert await _statuses_announced(gone.operation.id) == ["queued", "failed"]
    assert (await _reload(pending.operation.id)).status == "queued"
    assert (
        await operation_service.reap_lost_queued_operations(100, operation_job_known(connection))
        == []
    )


async def test_an_operation_past_the_cap_is_failed_even_while_rq_holds_its_job(
    operations_queue, monkeypatch
):
    """The backstop: a job RQ still holds but that will never run (say it
    failed before claiming) cannot keep its operation queued past the cap.
    A recent one is left alone."""
    connection, enqueue = operations_queue
    stale = await _request("op-reap-stale@example.com")
    recent = await _request("op-reap-recent@example.com")
    for requested in (stale, recent):
        await _mark_dispatched(requested.operation.id)
        enqueue(requested.operation.id)
    await _requested_ago(
        stale.operation.id, operation_service.MAX_QUEUED_AGE + timedelta(minutes=1)
    )

    lost = await operation_service.reap_lost_queued_operations(100, operation_job_known(connection))
    capped = await operation_service.reap_queued_past_cap(batch_size=100)

    assert lost == []
    assert capped == [stale.operation.id]
    failed = await _reload(stale.operation.id)
    assert failed.status == "failed"
    assert failed.error_code == DISPATCH_FAILED
    assert await _statuses_announced(stale.operation.id) == ["queued", "failed"]
    assert (await _reload(recent.operation.id)).status == "queued"

    # A late delivery of the failed operation does nothing.
    handler = _CountingHandler()
    _replace_redteam_handler(monkeypatch, handler)
    await run_operation(str(stale.operation.id))
    assert handler.calls == []
    assert (await _reload(stale.operation.id)).status == "failed"
    # The tender can request a red team again.
    again = await operation_service.request_operation(
        failed.organization_id, failed.tender_id, "redteam_run", failed.requested_by
    )
    assert again.created is True
    assert again.operation.id != stale.operation.id
    assert await operation_service.reap_queued_past_cap(batch_size=100) == []


async def test_an_llm_timeout_ends_the_operation_failed_with_llm_timeout(monkeypatch):
    async def times_out(operation: AsyncOperation) -> None:
        raise LLMTimeoutError("fake: provider request timed out after 60 s")

    _replace_redteam_handler(monkeypatch, times_out)
    requested = await _request("op-timeout@example.com")

    await run_operation(str(requested.operation.id))

    final = await _reload(requested.operation.id)
    assert final.status == "failed"
    assert final.error_code == "LLM_TIMEOUT"
    # The safe message, not the exception text.
    assert final.error_message == operation_jobs._FAILURES["LLM_TIMEOUT"]
    assert "60 s" not in (final.error_message or "")


async def test_an_unexpected_error_ends_failed_with_a_generic_message(monkeypatch):
    async def breaks(operation: AsyncOperation) -> None:
        raise RuntimeError("fake internal detail that must not reach the web app")

    _replace_redteam_handler(monkeypatch, breaks)
    requested = await _request("op-internal@example.com")

    await run_operation(str(requested.operation.id))

    final = await _reload(requested.operation.id)
    assert (final.status, final.error_code) == ("failed", "INTERNAL_ERROR")
    assert "fake internal detail" not in (final.error_message or "")


async def test_a_real_red_team_run_succeeds_through_the_worker(monkeypatch):
    """The real handler (redteam_service.run_red_team) with a scripted
    FakeLLMClient: a fresh tender has nothing to flag, so the run succeeds
    with no findings, and the critique agent was called once."""
    fake_client = FakeLLMClient()
    fake_client.script("redteam_critique", RedteamCritique, [RedteamCritique(findings=[])])
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake_client)
    requested = await _request("op-real@example.com")

    await run_operation(str(requested.operation.id))

    assert (await _reload(requested.operation.id)).status == "succeeded"
    assert [c["agent_name"] for c in fake_client.calls] == ["redteam_critique"]


async def test_a_real_proposal_generation_runs_once_under_a_duplicate_delivery(monkeypatch):
    """Stage 2. The real handler (proposal_service.draft_proposal_for_tender):
    a fresh tender drafts only the always-on Company Overview, one LLM call.
    Two drafts are scripted, so a second delivery that wrongly ran the work
    would succeed and be counted; it must not run."""
    fake_client = FakeLLMClient()
    fake_client.script(
        "proposal_drafting",
        ProposalSectionDraft,
        [ProposalSectionDraft(content="Fake overview.", claims=[])] * 2,
    )
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake_client)
    requested = await _request("op-proposal@example.com", "proposal_generation")
    op_id = requested.operation.id

    await run_operation(str(op_id))
    await run_operation(str(op_id))

    assert (await _reload(op_id)).status == "succeeded"
    assert [c["agent_name"] for c in fake_client.calls] == ["proposal_drafting"]
    sections = await proposal_service.list_proposal_sections_for_tender(
        requested.operation.tender_id
    )
    assert [s.section_name for s in sections] == ["Company Overview"]


async def test_a_real_commercial_validation_runs_once_and_explains_only_new_findings(
    monkeypatch,
):
    """Stage 2. The real handler (commercial_service.evaluate_commercial_for_
    tender) on a real pricing workbook: pricing_schedule_1.xlsx yields 4
    findings, so the first run makes 4 explanation calls. A duplicate
    delivery does nothing; a later re-validation (a new operation) finds
    all 4 stored by fingerprint and makes no LLM call."""
    fake_client = FakeLLMClient()
    fake_client.script(
        "commercial_explanation",
        FindingExplanation,
        [FindingExplanation(severity="major", explanation="Fake explanation.")] * 8,
    )
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda: fake_client)
    requested = await _request("op-commercial@example.com", "commercial_validation")
    op = requested.operation
    await document_service.create_document(
        tender_id=op.tender_id,
        organization_id=op.organization_id,
        actor_user_id=uuid.uuid4(),
        filename="pricing_schedule_1.xlsx",
        content=(_PRICING_FIXTURES / "pricing_schedule_1.xlsx").read_bytes(),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    await run_operation(str(op.id))
    await run_operation(str(op.id))

    assert (await _reload(op.id)).status == "succeeded"
    assert len(fake_client.calls) == 4
    findings = await commercial_service.list_pricing_findings_for_tender(op.tender_id)
    assert len(findings) == 4

    again = await operation_service.request_operation(
        op.organization_id, op.tender_id, "commercial_validation", op.requested_by
    )
    assert again.created is True
    await run_operation(str(again.operation.id))

    assert (await _reload(again.operation.id)).status == "succeeded"
    assert len(fake_client.calls) == 4, "an already-stored finding must not be explained again"
    assert len(await commercial_service.list_pricing_findings_for_tender(op.tender_id)) == 4
