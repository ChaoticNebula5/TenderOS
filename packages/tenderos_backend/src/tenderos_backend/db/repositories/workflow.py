import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, exists, func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import (
    ACTIVE_RUN_STATUSES,
    RUN_JOB_TIMEOUT,
    TERMINAL_RUN_STATUSES,
    ApprovalGate,
    WorkflowRun,
    WorkflowTask,
    WorkflowTaskDependency,
)
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_WORKFLOW_RUN,
    AGGREGATE_WORKFLOW_TASK,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_WORKFLOW_RUN_CREATED,
    EVENT_WORKFLOW_RUN_STATUS_CHANGED,
    EVENT_WORKFLOW_TASK_COMPLETED,
    EVENT_WORKFLOW_TASK_CREATED,
    EVENT_WORKFLOW_TASK_FAILED,
    EVENT_WORKFLOW_TASK_STARTED,
    workflow_run_status_changed_idempotency_key,
    workflow_run_status_changed_payload,
    workflow_task_completed_idempotency_key,
    workflow_task_completed_payload,
    workflow_task_created_idempotency_key,
    workflow_task_created_payload,
    workflow_task_failed_idempotency_key,
    workflow_task_failed_payload,
    workflow_task_started_idempotency_key,
    workflow_task_started_payload,
)

# Phase 4 plan item 77 (user decision, 2026-11-14): when a run reaches a
# terminal status, its tasks that never finished are "skipped", so no task
# reads Queued or Running under a run that has ended.
_UNFINISHED_TASK_STATUSES = ("pending", "running")


@dataclass(frozen=True)
class RunToCheck:
    """A queued or running run the reaper looks at (plan item 19), with the
    current job it saw and, for a running run, how long ago that job was
    recorded (database clock; None for a queued run)."""

    run_id: uuid.UUID
    status: str
    current_job_id: str | None
    job_age: timedelta | None = None


@dataclass(frozen=True)
class RunAtGate:
    """A run waiting for a human whose latest gate is decided (plan item
    19's gate-aware rule), with the current job it saw."""

    run_id: uuid.UUID
    current_job_id: str | None
    gate_id: uuid.UUID


class RunNoLongerActiveError(Exception):
    """A write for a run that is completed, failed or cancelled was refused
    (plan item 44): a status write, or opening a gate. The workflow jobs
    catch it, log one INFO line and return normally."""

    def __init__(self, run_id: uuid.UUID, attempted: str) -> None:
        super().__init__(f"workflow run {run_id} is no longer active; refused {attempted}")
        self.run_id = run_id
        self.attempted = attempted


class WorkflowRepository:
    """update_run_status/create_task/mark_task_running/mark_task_completed/
    mark_task_failed each also write a sibling outbox row, in the same session and
    transaction as the state change itself - a deliberate exception to this
    codebase's usual "repository touches only its own table, the service
    layer orchestrates cross-table writes" shape (see document_service.py's
    create_document for that usual shape). Chosen deliberately, not by
    default: these 4 methods are called directly from ~36 sites across
    orchestration/nodes/ with no shared service-layer wrapper in between
    (sync_run_status is the one partial exception, and even it just calls
    update_run_status once). This project's own history is a long list of a
    correct pattern applied at one call site and silently not its siblings
    (request_id at 4 of 5 knowledge-import callers, session.refresh() in 2
    repository methods but not a third, create_document's unconditional
    event where a sibling branched correctly - see KNOWN_LIMITATIONS.md).
    Writing the outbox row inside these 4 methods means every one of those
    ~36 call sites is covered by construction, not by 36 individually-
    correct edits nobody can guarantee stays correct as new callers are
    added. The notification therefore can never commit without the state
    change it describes - if a future caller wraps one of these in a
    transaction that then rolls back, the outbox row rolls back with it,
    same guarantee every other outbox write in this codebase already has.
    Do not "clean this up" into a separate service-layer call without
    re-solving the coverage problem this shape exists to close.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._outbox = OutboxRepository(session)

    async def create_run(
        self,
        tender_id: uuid.UUID,
        prompt_bundle_version: str,
        model_config: dict,
    ) -> WorkflowRun:
        # langgraph_thread_id has no derivation rule in the spec beyond "unique
        # text column" - generating the row's id client-side and reusing it as
        # the thread id keeps run <-> thread traceable one-to-one in a single
        # insert (no placeholder-then-update step, which would race under
        # concurrent creates on the unique constraint).
        run_id = uuid.uuid4()
        run = WorkflowRun(
            id=run_id,
            tender_id=tender_id,
            langgraph_thread_id=str(run_id),
            status="queued",
            prompt_bundle_version=prompt_bundle_version,
            model_config_json=model_config,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> WorkflowRun | None:
        return await self._session.get(WorkflowRun, run_id)

    async def list_runs_for_tender(self, tender_id: uuid.UUID) -> list[WorkflowRun]:
        # A list, not a single "current" run - a tender can have several
        # runs over its life, and "current" is ambiguous once the last one
        # is terminal or cancelled. Ordered newest-first so the most common
        # caller need (the latest run) doesn't require the client to sort.
        result = await self._session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.tender_id == tender_id)
            .order_by(WorkflowRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_active_run_for_tender(self, tender_id: uuid.UUID) -> WorkflowRun | None:
        # At most one row, by uq_workflow_runs_one_active_per_tender.
        result = await self._session.execute(
            select(WorkflowRun).where(
                WorkflowRun.tender_id == tender_id,
                WorkflowRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        return result.scalar_one_or_none()

    async def update_run_status(self, run_id: uuid.UUID, status: str) -> bool:
        """Write a run's status, unless it is already completed, failed or
        cancelled: one conditional UPDATE, so a write racing a cancel can
        never revive the run (plan item 44, KNOWN_LIMITATIONS' cancellation
        race). Returns whether it was written; the status event is written
        only then. Raises ValueError for a run that does not exist."""
        values: dict = {"status": status}
        if status == "running":
            values["started_at"] = func.coalesce(WorkflowRun.started_at, func.now())
        if status in TERMINAL_RUN_STATUSES:
            values["completed_at"] = func.now()
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.status.not_in(TERMINAL_RUN_STATUSES),
            )
            .values(**values)
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            if await self._session.get(WorkflowRun, run_id) is None:
                raise ValueError(f"WorkflowRun {run_id} not found")
            return False
        # A run already loaded in this session (sessions do not expire on
        # commit) is reloaded, so it shows what the database now holds and
        # can be read after the session closes.
        await self._session.get(WorkflowRun, run_id, populate_existing=True)
        if status in TERMINAL_RUN_STATUSES:
            await self._skip_unfinished_tasks(run_id)
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run_id,
            event_type=EVENT_WORKFLOW_RUN_STATUS_CHANGED,
            payload=workflow_run_status_changed_payload(run_id, status),
            idempotency_key=workflow_run_status_changed_idempotency_key(run_id),
        )
        return True

    async def is_active_for_update(self, run_id: uuid.UUID) -> bool:
        """Whether the run is active, holding a share lock on its row until
        this transaction ends. A cancel's status UPDATE then waits for this
        transaction, so a gate opened here is one the cancel will see and
        close; and once a cancel has committed, this returns False."""
        result = await self._session.execute(
            select(WorkflowRun.status).where(WorkflowRun.id == run_id).with_for_update(read=True)
        )
        status = result.scalar_one_or_none()
        return status in ACTIVE_RUN_STATUSES

    async def claim_queued_run(self, run_id: uuid.UUID, job_id: str) -> bool:
        """Phase 4 blocker 3: move a run from 'queued' to 'running' if, and
        only if, it is still 'queued'. Returns whether this call made the
        move. The outbox delivers WorkflowRunCreated at least once, so
        run_workflow can be called twice for one run, possibly at the same
        time; only the caller that wins this claim may invoke the graph.

        One conditional UPDATE, the same compare-and-set shape as
        IdempotencyRepository.take_over_expired: a concurrent second claim
        waits on the row lock, then re-checks status = 'queued' against the
        row the first one wrote and matches nothing. A cancelled run is not
        'queued' either, so it is never claimed. On success it writes the
        same WorkflowRunStatusChanged outbox event update_run_status does,
        so the progress stream sees the move to running.

        Plan item 19: the same UPDATE records `job_id` (the start job's own
        id) as the run's current job, for the run reaper.
        """
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.status == "queued")
            .values(
                status="running",
                started_at=func.coalesce(WorkflowRun.started_at, func.now()),
                current_job_id=job_id,
                current_job_at=func.now(),
            )
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        claimed = result.scalar_one_or_none() is not None
        if claimed:
            await self._announce_running(run_id)
        return claimed

    async def claim_waiting_run(self, run_id: uuid.UUID, job_id: str) -> bool:
        """Consumer guard for resume_workflow: move a run from
        'waiting_for_human' to 'running' if, and only if, it is still
        waiting. Same compare-and-set shape as claim_queued_run, so of two
        concurrent resumes only one matches. resume_workflow calls this only
        after confirming the thread is paused at the interrupt for the
        decided gate, so a run waiting at a different gate is never flipped
        to running by a stale resume. Records `job_id` (the resume job's
        own id) as the current job, like claim_queued_run."""
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.status == "waiting_for_human")
            .values(status="running", current_job_id=job_id, current_job_at=func.now())
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        claimed = result.scalar_one_or_none() is not None
        if claimed:
            await self._announce_running(run_id)
        return claimed

    async def _announce_running(self, run_id: uuid.UUID) -> None:
        # The same WorkflowRunStatusChanged event update_run_status writes,
        # so the progress stream sees a claim's move to running.
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run_id,
            event_type=EVENT_WORKFLOW_RUN_STATUS_CHANGED,
            payload=workflow_run_status_changed_payload(run_id, "running"),
            idempotency_key=workflow_run_status_changed_idempotency_key(run_id),
        )

    async def update_plan_version(self, run_id: uuid.UUID, plan_version: int) -> None:
        # B16: workflow_runs.plan_version stayed 0 forever while the
        # LangGraph checkpoint's own plan_version advanced -
        # supervisor_node.py's own return value was never written back to
        # Postgres. No outbox event, unlike update_run_status above -
        # nothing currently reads a "plan version changed" signal, and
        # this column existing at all is B12-forward-looking (a future
        # readiness report wanting to know which planning epoch a run's
        # data reflects), not something this write needs to announce yet.
        run = await self._session.get(WorkflowRun, run_id)
        if run is None:
            raise ValueError(f"WorkflowRun {run_id} not found")
        run.plan_version = plan_version

    async def try_reserve_retry_budget(
        self, run_id: uuid.UUID, cost: int, ceiling: int
    ) -> int | None:
        """Replaces the old increment_retry_count, which was a real,
        CodeRabbit-caught instance of this project's own standing
        "sequential retry tested, genuine concurrency untested" pattern -
        session.get() + a Python-level `run.retry_count += by` + flush is
        not atomic at either level: not the ceiling check (two concurrent
        _handle_graph_failure calls can both read the same stale
        retry_count, both see budget available, and both proceed) and not
        even the increment itself (a plain last-write-wins on the ORM
        attribute, the exact "lost update" shape bump_state_version had
        before its own FOR UPDATE fix).

        One atomic UPDATE does both the check and the reservation in a
        single round-trip: the WHERE clause's own `retry_count + :cost <=
        :ceiling` is evaluated against the row Postgres is already
        locking for this UPDATE, so a concurrent second caller's own
        UPDATE simply blocks until the first commits, then re-evaluates
        the WHERE clause against the NEW value - it cannot see the same
        stale ceiling-satisfying state twice. Returns the run's new
        retry_count when the reservation happened (a row was matched and
        updated), None otherwise - callers must treat None as "budget
        exhausted or already spent by a concurrent caller," not proceed to
        schedule a retry regardless. Plan item 19: the new count numbers the
        retry's RQ job (workflow_service.retry_job_id), unique per retry.
        """
        # .returning() + checking whether a row came back, not .rowcount -
        # AsyncSession.execute()'s own static return type doesn't narrow
        # to something mypy sees a rowcount attribute on, and this reads
        # more directly as "did the reservation take" anyway.
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.retry_count + cost <= ceiling)
            .values(retry_count=WorkflowRun.retry_count + cost)
            .returning(WorkflowRun.retry_count)
        )
        await self._session.flush()
        return result.scalar_one_or_none()

    async def record_retry_job(
        self, run_id: uuid.UUID, failing_job_id: str | None, retry_job_id: str
    ) -> bool:
        """Plan item 19: make `retry_job_id` the run's current job, if, and
        only if, the run is still running under the job that is failing.
        Called in the same transaction as the budget reservation, which
        commits before the retry is enqueued under that id. Returns whether
        it was recorded; when not (the run was reaped, cancelled or finished
        meanwhile), no retry may be enqueued.

        One conditional UPDATE, the compare-and-set shape of the claims. IS
        NOT DISTINCT FROM, so a retry scheduled before migration 024 (no job
        id of its own, on a run with none recorded) can still schedule the
        next one."""
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.status == "running",
                WorkflowRun.current_job_id.is_not_distinct_from(failing_job_id),
            )
            .values(current_job_id=retry_job_id, current_job_at=func.now())
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return False
        # Plan item 77: the failed attempt's graph call has returned, so no
        # task of this run is executing. A task still "running" is a sibling
        # LangGraph cancelled when another branch failed; the retry reruns
        # it, so until then it is pending again, not Running.
        await self._session.execute(
            sa_update(WorkflowTask)
            .where(WorkflowTask.workflow_run_id == run_id, WorkflowTask.status == "running")
            .values(status="pending")
        )
        return True

    async def fail_lost_run(
        self,
        run_id: uuid.UUID,
        status_seen: str,
        job_id_seen: str | None,
        error_code: str,
    ) -> bool:
        """Plan item 19: fail a run the reaper found lost, with `error_code`,
        if, and only if, it still has the status and current job the reaper
        checked. The check (RQ, then this) is not atomic, so the write names
        what was checked: a claim that moved the run on, or a retry recorded
        meanwhile, makes it match nothing. `status_seen` is always an active
        status, so a completed, failed or cancelled run is never touched.
        Writes the status event only when it matched; returns whether it
        did."""
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                WorkflowRun.status == status_seen,
                WorkflowRun.current_job_id.is_not_distinct_from(job_id_seen),
            )
            .values(
                status="failed",
                completed_at=func.now(),
                error_code=error_code,
                current_job_id=None,
            )
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            return False
        await self._skip_unfinished_tasks(run_id)
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run_id,
            event_type=EVENT_WORKFLOW_RUN_STATUS_CHANGED,
            payload=workflow_run_status_changed_payload(run_id, "failed"),
            idempotency_key=workflow_run_status_changed_idempotency_key(run_id),
        )
        return True

    async def list_runs_to_check(self, batch_size: int) -> list[RunToCheck]:
        """Plan item 19, the reaper's candidates whose RQ job it will ask
        about, oldest first, at most batch_size of each kind:
        - queued runs whose WorkflowRunCreated was dispatched (so their
          start job was enqueued). One whose event is still pending has had
          no job yet (the outbox may be retrying) and is not listed;
        - running runs with a recorded current job, with how long ago it
          was recorded (database clock)."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        queued = await self._session.execute(
            select(WorkflowRun.id, WorkflowRun.current_job_id)
            .join(
                OutboxEvent,
                and_(
                    OutboxEvent.aggregate_type == AGGREGATE_WORKFLOW_RUN,
                    OutboxEvent.aggregate_id == WorkflowRun.id,
                    OutboxEvent.event_type == EVENT_WORKFLOW_RUN_CREATED,
                ),
            )
            .where(WorkflowRun.status == "queued", OutboxEvent.status == "dispatched")
            .order_by(WorkflowRun.created_at)
            .limit(batch_size)
        )
        running = await self._session.execute(
            select(
                WorkflowRun.id,
                WorkflowRun.current_job_id,
                (func.now() - WorkflowRun.current_job_at).label("job_age"),
            )
            .where(WorkflowRun.status == "running", WorkflowRun.current_job_id.is_not(None))
            .order_by(WorkflowRun.current_job_at)
            .limit(batch_size)
        )
        return [
            RunToCheck(run_id=row.id, status="queued", current_job_id=row.current_job_id)
            for row in queued
        ] + [
            RunToCheck(
                run_id=row.id,
                status="running",
                current_job_id=row.current_job_id,
                job_age=row.job_age,
            )
            for row in running
        ]

    async def list_waiting_runs_with_a_decided_gate(self, batch_size: int) -> list[RunAtGate]:
        """Plan item 19, the gate-aware rule's candidates: runs waiting for a
        human whose latest gate is already decided and whose
        GateDecisionRecorded was dispatched, so a resume job was enqueued.
        Normally that job claims the run within moments; the reaper asks RQ
        whether it still can. Oldest decision first, at most batch_size.

        Only runs with a recorded current job: whether the resume already
        claimed the run is read from current_job_id, and a run from before
        migration 024 has none. Such a run may be waiting on purpose (until
        plan item 71, a resume that answered "request changes" ended the run
        at its gate and left it waiting; migration 029 moved those to the
        terminal changes_requested), so it is left alone. Observed
        2026-11-04: a pre-024 dev run waiting after a "request changes"
        decision, its resume job gone with a Redis flush, was failed by the
        first build of this rule."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        latest_gate = select(
            ApprovalGate.id,
            ApprovalGate.workflow_run_id,
            ApprovalGate.status,
            func.row_number()
            .over(
                partition_by=ApprovalGate.workflow_run_id,
                order_by=(ApprovalGate.requested_at.desc(), ApprovalGate.id.desc()),
            )
            .label("newest_first"),
        ).subquery("latest_gate")
        result = await self._session.execute(
            select(WorkflowRun.id, WorkflowRun.current_job_id, latest_gate.c.id.label("gate_id"))
            .join(
                latest_gate,
                and_(
                    latest_gate.c.workflow_run_id == WorkflowRun.id,
                    latest_gate.c.newest_first == 1,
                ),
            )
            .join(
                OutboxEvent,
                and_(
                    OutboxEvent.aggregate_type == AGGREGATE_APPROVAL_GATE,
                    OutboxEvent.aggregate_id == latest_gate.c.id,
                    OutboxEvent.event_type == EVENT_GATE_DECISION_RECORDED,
                ),
            )
            .where(
                WorkflowRun.status == "waiting_for_human",
                WorkflowRun.current_job_id.is_not(None),
                latest_gate.c.status != "pending",
                OutboxEvent.status == "dispatched",
            )
            .order_by(OutboxEvent.dispatched_at)
            .limit(batch_size)
        )
        return [
            RunAtGate(run_id=row.id, current_job_id=row.current_job_id, gate_id=row.gate_id)
            for row in result
        ]

    async def list_runs_past_cap(self, cap: timedelta, batch_size: int) -> list[RunToCheck]:
        """Plan item 19, the backstop: queued runs created more than `cap`
        ago, and running runs whose current job was recorded more than `cap`
        ago (database clock), whatever RQ says, oldest first, at most
        batch_size of each. A running run's age counts from its current
        job, not from when it started: a run resumed an hour ago after
        days at a gate is an hour old here."""
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        queued = await self._session.execute(
            select(WorkflowRun.id, WorkflowRun.current_job_id)
            .where(WorkflowRun.status == "queued", WorkflowRun.created_at < func.now() - cap)
            .order_by(WorkflowRun.created_at)
            .limit(batch_size)
        )
        job_at = func.coalesce(WorkflowRun.current_job_at, WorkflowRun.created_at)
        running = await self._session.execute(
            select(WorkflowRun.id, WorkflowRun.current_job_id)
            .where(WorkflowRun.status == "running", job_at < func.now() - cap)
            .order_by(job_at)
            .limit(batch_size)
        )
        return [
            RunToCheck(run_id=row.id, status="queued", current_job_id=row.current_job_id)
            for row in queued
        ] + [
            RunToCheck(run_id=row.id, status="running", current_job_id=row.current_job_id)
            for row in running
        ]

    async def mark_failed_if_not_terminal(self, run_id: uuid.UUID) -> bool:
        """Atomic counterpart to update_run_status for exactly one caller:
        mark_run_failed_on_timeout (domain/services/workflow_service.py).
        RQ's own JobTimeoutException can fire after a concurrent
        cancel_workflow_run has already moved this same run to "cancelled" -
        a session.get() + Python-level status check has the identical lost-
        update shape try_reserve_retry_budget's own docstring above already
        covers: a plain read-then-write can still act on a status this
        session read before the concurrent cancel committed, overwriting a
        real cancellation with "failed" and backing it with a false
        "workflow_run_timed_out" audit event. One atomic UPDATE, gated by
        the WHERE clause seeing Postgres's actually-current row, closes that
        window the same way try_reserve_retry_budget's does. Returns
        whether the row was actually moved to "failed" - callers must skip
        writing anything that describes a change that didn't happen (e.g.
        the audit event) when this returns False. Plan item 19: with
        error_code JOB_TIMEOUT, which the screen explains under "Last run".
        """
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(
                WorkflowRun.id == run_id,
                # Plan item 87: the model's terminal set, not a hand-written
                # copy, which had missed item 71's changes_requested.
                WorkflowRun.status.not_in(TERMINAL_RUN_STATUSES),
            )
            .values(
                status="failed",
                completed_at=datetime.now(UTC),
                error_code=RUN_JOB_TIMEOUT,
                current_job_id=None,
            )
            .returning(WorkflowRun.id)
        )
        await self._session.flush()
        moved = result.scalar_one_or_none() is not None
        if moved:
            await self._skip_unfinished_tasks(run_id)
            await self._outbox.create(
                aggregate_type=AGGREGATE_WORKFLOW_RUN,
                aggregate_id=run_id,
                event_type=EVENT_WORKFLOW_RUN_STATUS_CHANGED,
                payload=workflow_run_status_changed_payload(run_id, "failed"),
                idempotency_key=workflow_run_status_changed_idempotency_key(run_id),
            )
        return moved

    async def _skip_unfinished_tasks(self, run_id: uuid.UUID) -> None:
        """Plan item 77: the run just reached a terminal status, in this
        transaction; every task of it still pending or running becomes
        "skipped". Called from each of the three writes that end a run
        (update_run_status, fail_lost_run, mark_failed_if_not_terminal), so
        a graph failure, a cancel, Request changes, a timeout and the reaper
        all end the same way. No task event: the run's own status event,
        written in the same transaction, makes an open page re-read its
        tasks. Nothing reruns a terminal run's tasks (the start, resume and
        retry jobs each refuse a run that is not active), so nothing has to
        turn "skipped" back."""
        await self._session.execute(
            sa_update(WorkflowTask)
            .where(
                WorkflowTask.workflow_run_id == run_id,
                WorkflowTask.status.in_(_UNFINISHED_TASK_STATUSES),
            )
            .values(status="skipped")
        )

    async def list_tasks(self, workflow_run_id: uuid.UUID) -> list[WorkflowTask]:
        result = await self._session.execute(
            select(WorkflowTask)
            .where(WorkflowTask.workflow_run_id == workflow_run_id)
            .order_by(WorkflowTask.priority.desc(), WorkflowTask.created_at)
        )
        return list(result.scalars().all())

    async def get_task_by_key(
        self, workflow_run_id: uuid.UUID, task_key: str
    ) -> WorkflowTask | None:
        result = await self._session.execute(
            select(WorkflowTask).where(
                WorkflowTask.workflow_run_id == workflow_run_id,
                WorkflowTask.task_key == task_key,
            )
        )
        return result.scalar_one_or_none()

    async def create_task(
        self,
        workflow_run_id: uuid.UUID,
        task_key: str,
        task_type: str,
        assigned_agent: str,
        priority: int,
        input_refs: dict,
    ) -> WorkflowTask:
        task = WorkflowTask(
            workflow_run_id=workflow_run_id,
            task_key=task_key,
            task_type=task_type,
            assigned_agent=assigned_agent,
            priority=priority,
            input_refs=input_refs,
        )
        self._session.add(task)
        await self._session.flush()
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_TASK,
            aggregate_id=task.id,
            event_type=EVENT_WORKFLOW_TASK_CREATED,
            payload=workflow_task_created_payload(workflow_run_id, task.id, task_type),
            idempotency_key=workflow_task_created_idempotency_key(task.id),
        )
        return task

    async def add_task_dependency(self, task_id: uuid.UUID, depends_on_task_id: uuid.UUID) -> None:
        self._session.add(
            WorkflowTaskDependency(task_id=task_id, depends_on_task_id=depends_on_task_id)
        )
        await self._session.flush()

    async def mark_task_running(self, task_id: uuid.UUID) -> bool:
        """Plan item 77: a task node calls this as it begins its work, in a
        transaction that commits before the work, so the task list reads
        Running while it runs instead of Queued. started_at keeps the first
        start; a retried task keeps the time it first started.

        One conditional UPDATE, written only while the task's run is active
        and the task is not "skipped": a cancel can land while a node is
        about to start (cancellation is checked at gates, not inside
        analysis nodes), and that start must not turn a task of a cancelled
        run back to Running. The "skipped" test also holds when the two
        race: Postgres re-checks it on the row the cancel just wrote.
        Writes WorkflowTaskStarted only when it wrote the row; returns
        whether it did."""
        run_is_active = exists(
            select(WorkflowRun.id).where(
                WorkflowRun.id == WorkflowTask.workflow_run_id,
                WorkflowRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        result = await self._session.execute(
            sa_update(WorkflowTask)
            .where(WorkflowTask.id == task_id, WorkflowTask.status != "skipped", run_is_active)
            .values(
                status="running",
                # The app's clock, as mark_task_completed/failed use for
                # completed_at, not the database's now(): a task's two
                # times must come from one clock (the Postgres container's
                # was measured 485 ms behind the host's on 2026-11-10), or
                # a start can read as after the end (observed in this test
                # suite before this line used one clock).
                started_at=func.coalesce(WorkflowTask.started_at, datetime.now(UTC)),
            )
            .returning(WorkflowTask.workflow_run_id, WorkflowTask.task_type)
        )
        row = result.one_or_none()
        if row is None:
            return False
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_TASK,
            aggregate_id=task_id,
            event_type=EVENT_WORKFLOW_TASK_STARTED,
            payload=workflow_task_started_payload(row.workflow_run_id, task_id, row.task_type),
            idempotency_key=workflow_task_started_idempotency_key(task_id),
        )
        return True

    async def mark_task_completed(self, task_id: uuid.UUID, output_refs: dict) -> None:
        task = await self._session.get(WorkflowTask, task_id)
        if task is None:
            raise ValueError(f"WorkflowTask {task_id} not found")
        task.status = "completed"
        task.output_refs = output_refs
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_TASK,
            aggregate_id=task_id,
            event_type=EVENT_WORKFLOW_TASK_COMPLETED,
            payload=workflow_task_completed_payload(task.workflow_run_id, task_id, task.task_type),
            idempotency_key=workflow_task_completed_idempotency_key(task_id),
        )

    async def mark_task_failed(self, task_id: uuid.UUID, error_code: str) -> None:
        task = await self._session.get(WorkflowTask, task_id)
        if task is None:
            raise ValueError(f"WorkflowTask {task_id} not found")
        task.status = "failed"
        task.error_code = error_code
        task.attempt_count += 1
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_TASK,
            aggregate_id=task_id,
            event_type=EVENT_WORKFLOW_TASK_FAILED,
            payload=workflow_task_failed_payload(task.workflow_run_id, task_id, task.task_type),
            idempotency_key=workflow_task_failed_idempotency_key(task_id),
        )
