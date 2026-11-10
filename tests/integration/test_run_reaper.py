"""Plan item 19 (decision 0035, redesigned): the workflow run reaper fails a
run whose RQ job will never move it on, and never one that is only waiting.

Real RQ on the test Redis's "workflow" queue (no worker runs in the test
process, so a job put somewhere stays there) and real Postgres. Registry
states are built the way item 44's tests built them
(test_async_operations.py): Execution.create for a live started entry, a
past score for a dead one, enqueue_in for scheduled, the failed and finished
registries with the job's status set, job.cancel(). The RQ check is the
dispatcher's own (outbox_dispatcher.RunJobInspector); the rule and the
writes are run_reaper's.

Other tests leave runs active with jobs RQ never had, and a reap pass fails
those too, so these tests look only at their own runs, and pass a batch size
large enough to reach them.
"""

import logging
import time
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from _helpers import make_ready_run_inputs, register_org_and_tender
from redis import Redis
from rq import Queue
from rq.executions import Execution
from rq.job import JobStatus
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import (
    RUN_DISPATCH_FAILED,
    RUN_WORKER_LOST,
    ApprovalGate,
    WorkflowRun,
)
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_WORKFLOW_RUN_CREATED,
    EVENT_WORKFLOW_RUN_STATUS_CHANGED,
    gate_decision_recorded_idempotency_key,
    gate_decision_recorded_payload,
    workflow_run_created_idempotency_key,
    workflow_run_created_payload,
)
from tenderos_backend.domain.services import run_reaper, workflow_service
from tenderos_backend.domain.services.workflow_service import (
    resume_job_id,
    retry_job_id,
    start_job_id,
)
from tenderos_worker import outbox_dispatcher
from tenderos_worker.jobs import workflow as workflow_jobs
from tenderos_worker.jobs.workflow import retry_workflow_run, retry_workflow_run_job
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, RunJobInspector
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio

_EVERY_RUN = 10_000
_DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


class _Rq:
    """Puts jobs where a test needs them in the workflow queue, and takes
    them all out again afterwards."""

    def __init__(self) -> None:
        self.connection = Redis.from_url(settings.redis_url)
        self.queue = Queue("workflow", connection=self.connection)
        self._job_ids: list[str] = []

    def _enqueue(self, job_id: str):
        self._job_ids.append(job_id)
        return self.queue.enqueue(retry_workflow_run_job, "not-run", job_id=job_id)

    def queued(self, job_id: str) -> None:
        self._enqueue(job_id)

    def started(self, job_id: str, *, live: bool) -> None:
        """Popped and running. Live: RQ's own Execution record, its score a
        heartbeat expiry in the future. Dead: a score in the past, which no
        worker is refreshing."""
        job = self._enqueue(job_id)
        self.queue.remove(job)
        if live:
            with self.connection.pipeline() as pipe:
                Execution.create(job, ttl=600, pipeline=pipe)
                pipe.execute()
        else:
            member = f"{job_id}:{uuid.uuid4().hex}"
            self.connection.zadd(self.queue.started_job_registry.key, {member: time.time() - 30})
        assert job_id in self.queue.started_job_registry

    def move_started_score(self, job_id: str, by: float) -> None:
        key = self.queue.started_job_registry.key
        for member, score in self.connection.zrange(key, 0, -1, withscores=True):
            if member.decode().startswith(f"{job_id}:"):
                self.connection.zadd(key, {member: score + by})

    def scheduled(self, job_id: str) -> None:
        self._job_ids.append(job_id)
        self.queue.enqueue_in(
            timedelta(minutes=5), retry_workflow_run_job, "not-run", job_id=job_id
        )
        assert job_id in self.queue.scheduled_job_registry

    def failed(self, job_id: str) -> None:
        """What RQ's worker does with a job that raised and has no retries
        left (rq/worker/base.py, handle_job_failure)."""
        job = self._enqueue(job_id)
        self.queue.remove(job)
        job.set_status(JobStatus.FAILED)
        self.queue.failed_job_registry.add(job, ttl=3600, exc_string="Traceback: fake failure")

    def canceled(self, job_id: str) -> None:
        self._enqueue(job_id).cancel()

    def finished(self, job_id: str) -> None:
        job = self._enqueue(job_id)
        self.queue.remove(job)
        job.set_status(JobStatus.FINISHED)
        self.queue.finished_job_registry.add(job, ttl=500)

    def cleanup(self) -> None:
        queue = self.queue
        for job_id in self._job_ids:
            for registry in (
                queue.failed_job_registry,
                queue.canceled_job_registry,
                queue.scheduled_job_registry,
                queue.deferred_job_registry,
                queue.finished_job_registry,
            ):
                registry.remove(job_id)
            for member in self.connection.zrange(queue.started_job_registry.key, 0, -1):
                if member.decode().startswith(f"{job_id}:"):
                    self.connection.zrem(queue.started_job_registry.key, member)
            self.connection.lrem(queue.key, 0, job_id)
            job = queue.fetch_job(job_id)
            if job is not None:
                job.delete()
        self.connection.close()


@pytest.fixture
def rq():
    jobs = _Rq()
    yield jobs
    jobs.cleanup()


class _Clock:
    """The inspector's clock, moved forward by hand for the second look."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


async def _run(
    label: str,
    status: str = "running",
    job: Callable[[uuid.UUID], str] | None = start_job_id,
    job_age: timedelta = timedelta(0),
) -> uuid.UUID:
    """A run of a fresh tender in `status`, its current job `job(run_id)`
    recorded `job_age` ago (database clock)."""
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender.id, "v1", {})
        await uow.commit()
        run_id = run.id
    await _set(
        run_id,
        status=status,
        current_job_id=job(run_id) if job is not None else None,
        current_job_at=func.now() - job_age,
    )
    return run_id


async def _set(run_id: uuid.UUID, **values) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run_id).values(**values)
        )
        await uow.commit()


async def _reload(run_id: uuid.UUID) -> WorkflowRun:
    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
    assert run is not None
    return run


async def _announced(run_id: uuid.UUID) -> list[str]:
    async with UnitOfWork() as uow:
        payloads = (
            await uow.session.execute(
                select(OutboxEvent.payload)
                .where(
                    OutboxEvent.aggregate_id == run_id,
                    OutboxEvent.event_type == EVENT_WORKFLOW_RUN_STATUS_CHANGED,
                )
                .order_by(OutboxEvent.created_at)
            )
        ).scalars()
        return [p["status"] for p in payloads]


async def _reap_audits(run_id: uuid.UUID) -> list[dict]:
    async with UnitOfWork() as uow:
        rows = (
            await uow.session.execute(
                select(AuditEvent).where(
                    AuditEvent.workflow_run_id == run_id,
                    AuditEvent.action == "workflow_run_reaped",
                )
            )
        ).scalars()
        return [row.event_metadata for row in rows]


async def _reap(rq: _Rq, inspector: RunJobInspector | None = None) -> dict:
    inspector = inspector or RunJobInspector(rq.connection)
    reaped = await run_reaper.reap_lost_runs(_EVERY_RUN, inspector.verdict)
    return {r.run_id: r for r in reaped}


async def _created_event(run_id: uuid.UUID, *, status: str, attempt_count: int = 0) -> uuid.UUID:
    run = await _reload(run_id)
    async with UnitOfWork() as uow:
        event = await uow.outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run_id,
            event_type=EVENT_WORKFLOW_RUN_CREATED,
            payload=workflow_run_created_payload(run_id, run.tender_id),
            idempotency_key=workflow_run_created_idempotency_key(run_id),
        )
        event.status = status
        event.attempt_count = attempt_count
        await uow.commit()
        return event.id


async def _decided_gate(
    run_id: uuid.UUID,
    *,
    event_status: str = "dispatched",
    attempt_count: int = 0,
    gate_status: str = "approved",
) -> tuple[uuid.UUID, uuid.UUID]:
    """A gate on a waiting run, decided, with its GateDecisionRecorded
    event. Returns (gate id, event id)."""
    run = await _reload(run_id)
    gate_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        await uow.gates.create(
            gate_id, run.tender_id, run_id, "requirement_review:plan-1", "requirement_review", {}
        )
        await uow.session.execute(
            update(ApprovalGate)
            .where(ApprovalGate.id == gate_id)
            .values(
                status=gate_status,
                decision_payload={"decision": "approve"},
                decided_at=func.now(),
            )
        )
        event = await uow.outbox.create(
            aggregate_type=AGGREGATE_APPROVAL_GATE,
            aggregate_id=gate_id,
            event_type=EVENT_GATE_DECISION_RECORDED,
            payload=gate_decision_recorded_payload(gate_id, run_id),
            idempotency_key=gate_decision_recorded_idempotency_key(gate_id),
        )
        event.status = event_status
        event.attempt_count = attempt_count
        if event_status == "dispatched":
            event.dispatched_at = func.now()
        await uow.commit()
        return gate_id, event.id


# --- waiting is not lost -----------------------------------------------------


async def test_a_run_waiting_behind_another_tenders_long_job_is_not_reaped(rq):
    """Design test 1. Tender A's run waits for its retry, now in the queue
    list behind tender B's job, which is running (a live started entry).
    A's previous job, whose failure scheduled the retry, is in the failed
    registry, as it always is (I4). Its current job was recorded two hours
    ago, with no task progress since. Waiting, not lost."""
    waiting = await _run(
        "reap-behind", job=lambda r: retry_job_id(r, 1), job_age=timedelta(hours=2)
    )
    ahead = await _run("reap-ahead", job_age=timedelta(minutes=20))
    rq.failed(start_job_id(waiting))
    rq.queued(retry_job_id(waiting, 1))
    rq.started(start_job_id(ahead), live=True)

    reaped = await _reap(rq)

    assert waiting not in reaped and ahead not in reaped
    assert (await _reload(waiting)).status == "running"
    assert (await _reload(ahead)).status == "running"


async def test_a_scheduled_retry_with_the_failed_job_behind_it_is_not_reaped(rq):
    """Design test 2."""
    run_id = await _run(
        "reap-scheduled", job=lambda r: retry_job_id(r, 1), job_age=timedelta(hours=1)
    )
    rq.failed(start_job_id(run_id))
    rq.scheduled(retry_job_id(run_id, 1))

    assert run_id not in await _reap(rq)
    assert (await _reload(run_id)).status == "running"


async def test_a_live_started_job_is_left_alone(rq):
    """Design test 6: running, its heartbeat current."""
    run_id = await _run("reap-live", job_age=timedelta(hours=2))
    rq.started(start_job_id(run_id), live=True)

    assert run_id not in await _reap(rq)
    assert (await _reload(run_id)).status == "running"


# --- lost: RQ says the job will never run -----------------------------------


@pytest.mark.parametrize(
    ("where", "rule"),
    [
        ("failed", run_reaper.JOB_FAILED),
        ("canceled", run_reaper.JOB_CANCELED),
        ("finished", run_reaper.JOB_FINISHED),
    ],
)
async def test_a_current_job_rq_will_never_run_fails_its_run_as_worker_lost(rq, where, rule):
    """Design test 3. Recorded a moment ago, not hours: what RQ says is
    enough. Finished: the current job's last act is the run's status write,
    so a finished current job with the run still running ended without
    moving it on."""
    run_id = await _run(f"reap-{where}")
    getattr(rq, where)(start_job_id(run_id))

    reaped = await _reap(rq)

    assert reaped[run_id] == run_reaper.ReapedRun(
        run_id, RUN_WORKER_LOST, rule, start_job_id(run_id)
    )
    run = await _reload(run_id)
    assert (run.status, run.error_code, run.current_job_id) == ("failed", RUN_WORKER_LOST, None)
    assert run.completed_at is not None
    assert await _announced(run_id) == ["failed"]
    assert await _reap_audits(run_id) == [
        {"error_code": RUN_WORKER_LOST, "rule": rule, "job_id": start_job_id(run_id)}
    ]
    assert run_id not in await _reap(rq), "a reaped run is not reaped again"


async def test_a_current_job_unknown_to_rq_is_lost_only_after_the_grace(rq):
    """Design test 4. A retry's id is recorded before it is enqueued, so a
    job RQ does not know yet is not lost at once: 30 s after it was recorded
    it is left alone, 61 s after it is failed."""
    fresh = await _run(
        "reap-unknown-fresh", job=lambda r: retry_job_id(r, 1), job_age=timedelta(seconds=30)
    )
    gone = await _run(
        "reap-unknown-gone", job=lambda r: retry_job_id(r, 1), job_age=timedelta(seconds=61)
    )

    reaped = await _reap(rq)

    assert fresh not in reaped
    assert (await _reload(fresh)).status == "running"
    assert reaped[gone].rule == run_reaper.JOB_UNKNOWN
    assert (await _reload(gone)).error_code == RUN_WORKER_LOST


async def test_an_abandoned_job_is_reaped_on_the_second_look_only_if_its_score_never_moved(rq):
    """Design test 5. A started entry past due (no heartbeat) is a dead
    worker or a live one cut off from Redis. First look: suspected, not
    reaped. A second look two minutes later with the score unchanged:
    abandoned, reaped. A score that moved in between (a heartbeat after
    Redis came back) is a live worker: left alone."""
    dead = await _run("reap-abandoned-dead")
    alive = await _run("reap-abandoned-alive")
    rq.started(start_job_id(dead), live=False)
    rq.started(start_job_id(alive), live=False)
    clock = _Clock()
    inspector = RunJobInspector(rq.connection, clock=clock)

    first = await _reap(rq, inspector)
    assert dead not in first and alive not in first

    rq.move_started_score(start_job_id(alive), by=1.0)
    clock.now += outbox_dispatcher.ABANDONED_CONFIRM_SECONDS + 1
    second = await _reap(rq, inspector)

    assert second[dead].rule == run_reaper.JOB_ABANDONED
    assert (await _reload(dead)).error_code == RUN_WORKER_LOST
    assert alive not in second
    assert (await _reload(alive)).status == "running"


async def test_an_abandoned_job_is_not_reaped_before_the_confirmation_interval(rq):
    run_id = await _run("reap-abandoned-early")
    rq.started(start_job_id(run_id), live=False)
    clock = _Clock()
    inspector = RunJobInspector(rq.connection, clock=clock)

    assert run_id not in await _reap(rq, inspector)
    clock.now += outbox_dispatcher.ABANDONED_CONFIRM_SECONDS - 5
    assert run_id not in await _reap(rq, inspector)
    assert (await _reload(run_id)).status == "running"


# --- the cap -----------------------------------------------------------------


async def test_the_cap_fails_a_run_whose_current_job_is_six_hours_old_whatever_rq_says(rq):
    """Design test 7. RQ still holds each of these jobs in the queue list,
    so no RQ rule fires; the cap does, counted from the current job, not
    from when the run started: a run started 7 hours ago and resumed an
    hour ago is left alone."""
    cap = run_reaper.MAX_RUN_JOB_AGE
    old = await _run("reap-cap-old", job_age=cap + timedelta(minutes=1))
    young = await _run("reap-cap-young", job_age=cap - timedelta(minutes=1))
    resumed = await _run(
        "reap-cap-resumed", job=lambda r: resume_job_id(r, uuid.uuid4()), job_age=timedelta(hours=1)
    )
    await _set(resumed, started_at=func.now() - timedelta(hours=7))
    stuck_queued = await _run("reap-cap-queued", status="queued", job=None)
    await _set(stuck_queued, created_at=func.now() - cap - timedelta(minutes=1))
    for run_id in (old, young, resumed):
        rq.queued((await _reload(run_id)).current_job_id)

    lost = await _reap(rq)
    capped = {r.run_id: r for r in await run_reaper.reap_runs_past_cap(_EVERY_RUN)}

    assert not {old, young, resumed, stuck_queued} & lost.keys()
    assert capped[old].rule == run_reaper.RULE_CAP
    assert (await _reload(old)).error_code == RUN_WORKER_LOST
    assert capped[stuck_queued].error_code == RUN_DISPATCH_FAILED
    assert (await _reload(stuck_queued)).status == "failed"
    assert young not in capped and resumed not in capped
    assert (await _reload(young)).status == "running"
    assert (await _reload(resumed)).status == "running"


# --- the conditional fail ----------------------------------------------------


async def test_a_retry_recorded_after_the_check_keeps_its_run(rq):
    """Design test 8, job half. RQ is read, then Postgres is written: here a
    retry is recorded in between. The fail names the job it checked, so it
    matches nothing."""
    run_id = await _run("reap-cas-job")
    rq.failed(start_job_id(run_id))
    inspector = RunJobInspector(rq.connection)

    async def verdict_then_a_retry(job_id: str) -> str | None:
        verdict = await inspector.verdict(job_id)
        if job_id == start_job_id(run_id):
            async with UnitOfWork() as uow:
                assert await uow.workflow.record_retry_job(
                    run_id, start_job_id(run_id), retry_job_id(run_id, 1)
                )
                await uow.commit()
        return verdict

    reaped = await run_reaper.reap_lost_runs(_EVERY_RUN, verdict_then_a_retry)

    assert run_id not in {r.run_id for r in reaped}
    run = await _reload(run_id)
    assert (run.status, run.current_job_id) == ("running", retry_job_id(run_id, 1))
    assert await _announced(run_id) == []
    assert await _reap_audits(run_id) == []


async def test_a_run_finished_after_the_check_keeps_its_status(rq):
    """Design test 8, status half: the run completed in between, with its
    current job unchanged. The fail names the status it saw."""
    run_id = await _run("reap-cas-status")
    rq.finished(start_job_id(run_id))
    inspector = RunJobInspector(rq.connection)

    async def verdict_then_complete(job_id: str) -> str | None:
        verdict = await inspector.verdict(job_id)
        if job_id == start_job_id(run_id):
            async with UnitOfWork() as uow:
                assert await uow.workflow.update_run_status(run_id, "completed")
                await uow.commit()
        return verdict

    reaped = await run_reaper.reap_lost_runs(_EVERY_RUN, verdict_then_complete)

    assert run_id not in {r.run_id for r in reaped}
    assert (await _reload(run_id)).status == "completed"
    assert await _reap_audits(run_id) == []


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_a_terminal_run_is_never_reaped(rq, terminal):
    """Design test 9: its job is in the failed registry, and the fail is
    even called with its exact current job; it stays as it is."""
    run_id = await _run(f"reap-terminal-{terminal}", status=terminal)
    rq.failed(start_job_id(run_id))

    assert run_id not in await _reap(rq)
    async with UnitOfWork() as uow:
        failed = await uow.workflow.fail_lost_run(
            run_id, "running", start_job_id(run_id), RUN_WORKER_LOST
        )
        await uow.commit()

    assert failed is False
    run = await _reload(run_id)
    assert (run.status, run.error_code) == (terminal, None)
    assert await _announced(run_id) == []


# --- the claims record the job; the retry checks it --------------------------


async def test_the_claims_record_their_own_job_as_current():
    """Design test 10."""
    run_id = await _run("claim-records", status="queued", job=None)

    assert await workflow_service.claim_run_for_start(run_id)
    started = await _reload(run_id)
    assert started.current_job_id == start_job_id(run_id)
    assert started.current_job_at is not None

    gate_id = uuid.uuid4()
    await _set(run_id, status="waiting_for_human", current_job_at=None)
    assert await workflow_service.claim_run_for_resume(run_id, gate_id)
    resumed = await _reload(run_id)
    assert resumed.current_job_id == resume_job_id(run_id, gate_id)
    assert resumed.current_job_at is not None


@asynccontextmanager
async def _no_checkpointer():
    yield None


@pytest.mark.parametrize("case", ["failed", "cancelled", "another_job"])
async def test_a_retry_that_is_not_its_runs_current_job_does_not_start(monkeypatch, caplog, case):
    """Design test 12. The run was failed (by the timeout path or this
    reaper) or cancelled, or it runs under a different job: the retry fires
    and stops before building the graph, with one INFO line."""
    run_id = await _run(f"retry-check-{case}", job=lambda r: retry_job_id(r, 1))
    if case == "another_job":
        await _set(run_id, current_job_id=retry_job_id(run_id, 2))
    else:
        await _set(run_id, status=case)

    def graph_must_not_be_built(_checkpointer):
        raise AssertionError("the retry built the graph")

    monkeypatch.setattr(workflow_jobs, "get_checkpointer", _no_checkpointer)
    monkeypatch.setattr(workflow_jobs, "build_graph", graph_must_not_be_built)
    caplog.set_level(logging.INFO, logger=workflow_jobs.logger.name)

    await retry_workflow_run(str(run_id), 1)

    assert len([r for r in caplog.records if "not started" in r.getMessage()]) == 1


async def test_a_retry_that_is_its_runs_current_job_resumes_the_graph(monkeypatch):
    run_id = await _run("retry-check-current", job=lambda r: retry_job_id(r, 1))
    invocations: list = []

    class _Graph:
        async def ainvoke(self, value, config):
            invocations.append(value)

    monkeypatch.setattr(workflow_jobs, "get_checkpointer", _no_checkpointer)
    monkeypatch.setattr(workflow_jobs, "build_graph", lambda checkpointer: _Graph())

    await retry_workflow_run(str(run_id), 1)

    assert invocations == [None]


# --- queued runs and the dead letter -----------------------------------------


@pytest.mark.parametrize(
    ("where", "rule"),
    [("failed", run_reaper.JOB_FAILED), ("unknown", run_reaper.JOB_UNKNOWN)],
)
async def test_a_queued_run_whose_start_job_is_lost_fails_as_dispatch_failed(rq, where, rule):
    """Design test 14. Its WorkflowRunCreated was dispatched, so its start
    job was enqueued (the dispatcher marks the event only after the
    enqueue): failed before its claim, or gone from Redis. No grace."""
    run_id = await _run(f"reap-queued-{where}", status="queued", job=None)
    await _created_event(run_id, status="dispatched")
    if where == "failed":
        rq.failed(start_job_id(run_id))

    reaped = await _reap(rq)

    assert reaped[run_id] == run_reaper.ReapedRun(
        run_id, RUN_DISPATCH_FAILED, rule, start_job_id(run_id)
    )
    run = await _reload(run_id)
    assert (run.status, run.error_code) == ("failed", RUN_DISPATCH_FAILED)
    assert await _announced(run_id) == ["failed"]


async def test_a_queued_run_still_waiting_for_its_start_is_left_alone(rq):
    """Its start job is in the queue list, or its event is still pending
    (the outbox may be retrying it): waiting."""
    in_queue = await _run("reap-queued-waiting", status="queued", job=None)
    await _created_event(in_queue, status="dispatched")
    rq.queued(start_job_id(in_queue))
    pending = await _run("reap-queued-pending", status="queued", job=None)
    await _created_event(pending, status="pending")

    reaped = await _reap(rq)

    assert in_queue not in reaped and pending not in reaped
    assert (await _reload(in_queue)).status == "queued"
    assert (await _reload(pending)).status == "queued"


async def _dispatch_failing(monkeypatch, event_id: uuid.UUID) -> None:
    """One dispatch of this event whose enqueue fails (a non-infrastructure
    error, which uses up an attempt)."""

    def broken_enqueue(self, *args, **kwargs):
        raise ValueError("this job cannot be enqueued")

    connection = Redis.from_url(settings.redis_url)
    try:
        with monkeypatch.context() as patched:
            patched.setattr(Queue, "enqueue", broken_enqueue)
            await outbox_dispatcher._dispatch_claimed_event(
                outbox_dispatcher._queues(connection), event_id
            )
    finally:
        connection.close()


async def _event_status(event_id: uuid.UUID) -> str:
    async with UnitOfWork() as uow:
        event = await uow.outbox.get(event_id)
    assert event is not None
    return event.status


async def test_a_dead_lettered_start_fails_its_run_in_the_same_transaction(monkeypatch):
    """Design test 13, start. One failure short of its last attempt, one
    failed dispatch dead-letters the event and fails the run as
    DISPATCH_FAILED with it. One with attempts left is only retried."""
    stuck = await _run("dead-letter-start", status="queued", job=None)
    stuck_event = await _created_event(stuck, status="pending", attempt_count=MAX_ATTEMPTS - 1)
    retrying = await _run("dead-letter-start-retrying", status="queued", job=None)
    retrying_event = await _created_event(retrying, status="pending", attempt_count=2)

    await _dispatch_failing(monkeypatch, stuck_event)
    await _dispatch_failing(monkeypatch, retrying_event)

    assert await _event_status(stuck_event) == "failed"
    run = await _reload(stuck)
    assert (run.status, run.error_code) == ("failed", RUN_DISPATCH_FAILED)
    assert await _announced(stuck) == ["failed"]
    assert await _reap_audits(stuck) == [
        {
            "error_code": RUN_DISPATCH_FAILED,
            "rule": run_reaper.RULE_DEAD_LETTER,
            "job_id": start_job_id(stuck),
        }
    ]
    assert await _event_status(retrying_event) == "pending"
    assert (await _reload(retrying)).status == "queued"


async def test_a_dead_lettered_gate_decision_fails_its_waiting_run(monkeypatch):
    """Design test 13, resume: the decision was recorded but its resume was
    never enqueued, so the run would wait at a decided gate for good."""
    run_id = await _run("dead-letter-gate", status="waiting_for_human")
    gate_id, event_id = await _decided_gate(
        run_id, event_status="pending", attempt_count=MAX_ATTEMPTS - 1
    )

    await _dispatch_failing(monkeypatch, event_id)

    assert await _event_status(event_id) == "failed"
    run = await _reload(run_id)
    assert (run.status, run.error_code) == ("failed", RUN_DISPATCH_FAILED)
    assert await _reap_audits(run_id) == [
        {
            "error_code": RUN_DISPATCH_FAILED,
            "rule": run_reaper.RULE_DEAD_LETTER,
            "job_id": resume_job_id(run_id, gate_id),
        }
    ]


# --- waiting for a human, with the gate already decided ----------------------


@pytest.mark.parametrize("where", ["failed", "unknown", "finished"])
async def test_a_decided_gate_whose_resume_never_claimed_the_run_is_reaped(rq, where):
    """Decision 3, the gate-aware half. The gate is decided and its
    GateDecisionRecorded dispatched, but the resume job never claimed the
    run (its current job is still the one that opened the gate): it failed
    before its claim, is gone, or ended without claiming."""
    run_id = await _run(f"reap-gate-{where}", status="waiting_for_human")
    gate_id, _event_id = await _decided_gate(run_id)
    if where != "unknown":
        getattr(rq, where)(resume_job_id(run_id, gate_id))

    reaped = await _reap(rq)

    assert reaped[run_id].error_code == RUN_DISPATCH_FAILED
    assert reaped[run_id].job_id == resume_job_id(run_id, gate_id)
    run = await _reload(run_id)
    assert (run.status, run.error_code) == ("failed", RUN_DISPATCH_FAILED)
    assert await _announced(run_id) == ["failed"]


async def test_a_waiting_run_is_left_alone_when_its_resume_can_still_run_or_already_claimed(rq):
    """Left alone: the resume is queued; the resume claimed the run and
    ended it at the gate on purpose (changes_requested, the run waiting with
    the resume as its current job, now finished); the decision's event is
    still pending; the latest gate is not decided yet."""
    queued = await _run("reap-gate-queued", status="waiting_for_human")
    queued_gate, _ = await _decided_gate(queued)
    rq.queued(resume_job_id(queued, queued_gate))

    changes = await _run("reap-gate-changes", status="waiting_for_human")
    changes_gate, _ = await _decided_gate(changes, gate_status="changes_requested")
    await _set(changes, current_job_id=resume_job_id(changes, changes_gate))
    rq.finished(resume_job_id(changes, changes_gate))

    pending = await _run("reap-gate-pending-event", status="waiting_for_human")
    await _decided_gate(pending, event_status="pending")

    undecided = await _run("reap-gate-undecided", status="waiting_for_human")
    await _decided_gate(undecided, gate_status="pending", event_status="pending")

    reaped = await _reap(rq)

    for run_id in (queued, changes, pending, undecided):
        assert run_id not in reaped
        assert (await _reload(run_id)).status == "waiting_for_human"


async def test_a_waiting_run_from_before_migration_024_is_never_reaped_by_the_gate_rule(rq):
    """Observed 2026-11-04 in the fake stack, on the first build of the gate
    rule: a run from before migration 024 (no current job recorded),
    waiting after its resume answered "request changes", its resume job
    gone from Redis (flushed). Nothing tells whether that resume claimed the
    run, so the run, which may be waiting on purpose, is left alone."""
    run_id = await _run("reap-gate-pre-024", status="waiting_for_human", job=None)
    await _decided_gate(run_id, gate_status="changes_requested")

    assert run_id not in await _reap(rq)
    run = await _reload(run_id)
    assert (run.status, run.error_code) == ("waiting_for_human", None)
    assert await _reap_audits(run_id) == []


# --- the outcome: the tender can start again ---------------------------------


async def test_after_a_reap_the_tender_can_start_a_new_run_at_once(rq):
    """Design test 15: pins item 43's index to the reaper's outcome."""
    email = f"reap-new-run-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await make_ready_run_inputs(email, f"{email} org", f"{email} tender")

    def start():
        return workflow_service.create_workflow_run(
            tender.id, registered.organization.id, registered.user.id
        )

    stuck = await start()
    with pytest.raises(workflow_service.WorkflowRunActiveError):
        await start()
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == stuck.id,
                OutboxEvent.event_type == EVENT_WORKFLOW_RUN_CREATED,
            )
            .values(status="dispatched", dispatched_at=func.now())
        )
        await uow.commit()

    assert (await _reap(rq))[stuck.id].error_code == RUN_DISPATCH_FAILED

    again = await start()
    assert again.id != stuck.id
    assert again.status == "queued"


# --- contained failures ------------------------------------------------------


async def test_with_redis_down_the_rq_rules_are_skipped_and_the_cap_still_applies(caplog):
    """Design test 16, Redis: the RQ rules fail the pass (logged, nothing
    written), and the cap, which needs no Redis, still fails its run."""
    capped = await _run(
        "reap-redis-down", job_age=run_reaper.MAX_RUN_JOB_AGE + timedelta(minutes=1)
    )
    dead = Redis.from_url(_DEAD_REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)

    try:
        with caplog.at_level(logging.WARNING, logger=outbox_dispatcher.logger.name):
            await outbox_dispatcher._reap_lost_runs(RunJobInspector(dead))
    finally:
        dead.close()

    assert any("workflow run reaper failed" in r.getMessage() for r in caplog.records)
    run = await _reload(capped)
    assert (run.status, run.error_code) == ("failed", RUN_WORKER_LOST)
    assert await _reap_audits(capped) == [
        {"error_code": RUN_WORKER_LOST, "rule": run_reaper.RULE_CAP, "job_id": start_job_id(capped)}
    ]


async def test_a_database_error_in_the_run_reaper_is_logged_and_swallowed(monkeypatch, caplog):
    """Design test 16, Postgres: both passes fail, both are logged, and the
    call returns, so the dispatcher's loop goes on dispatching."""

    async def broken(*args, **kwargs):
        raise OperationalError("fake", {}, Exception("fake database outage"))

    monkeypatch.setattr(run_reaper, "reap_lost_runs", broken)
    monkeypatch.setattr(run_reaper, "reap_runs_past_cap", broken)
    connection = Redis.from_url(settings.redis_url)

    try:
        with caplog.at_level(logging.WARNING, logger=outbox_dispatcher.logger.name):
            await outbox_dispatcher._reap_lost_runs(RunJobInspector(connection))
    finally:
        connection.close()

    messages = [r.getMessage() for r in caplog.records]
    assert any("workflow run reaper failed" in m for m in messages)
    assert any("workflow run cap reaper failed" in m for m in messages)
