import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.workflow import (
    WorkflowRun,
    WorkflowTask,
    WorkflowTaskDependency,
)
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_WORKFLOW_RUN,
    AGGREGATE_WORKFLOW_TASK,
    EVENT_WORKFLOW_RUN_STATUS_CHANGED,
    EVENT_WORKFLOW_TASK_COMPLETED,
    EVENT_WORKFLOW_TASK_CREATED,
    EVENT_WORKFLOW_TASK_FAILED,
    workflow_run_status_changed_idempotency_key,
    workflow_run_status_changed_payload,
    workflow_task_completed_idempotency_key,
    workflow_task_completed_payload,
    workflow_task_created_idempotency_key,
    workflow_task_created_payload,
    workflow_task_failed_idempotency_key,
    workflow_task_failed_payload,
)


class WorkflowRepository:
    """update_run_status/create_task/mark_task_completed/mark_task_failed
    each also write a sibling outbox row, in the same session and
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

    async def update_run_status(self, run_id: uuid.UUID, status: str) -> None:
        run = await self._session.get(WorkflowRun, run_id)
        if run is None:
            raise ValueError(f"WorkflowRun {run_id} not found")
        run.status = status
        if status == "running" and run.started_at is None:
            run.started_at = datetime.now(UTC)
        if status in ("completed", "failed", "cancelled"):
            run.completed_at = datetime.now(UTC)
        await self._session.flush()
        await self._outbox.create(
            aggregate_type=AGGREGATE_WORKFLOW_RUN,
            aggregate_id=run_id,
            event_type=EVENT_WORKFLOW_RUN_STATUS_CHANGED,
            payload=workflow_run_status_changed_payload(run_id, status),
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

    async def try_reserve_retry_budget(self, run_id: uuid.UUID, cost: int, ceiling: int) -> bool:
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
        stale ceiling-satisfying state twice. Returns whether the
        reservation actually happened (a row was matched and updated) -
        callers must treat False as "budget exhausted or already spent by
        a concurrent caller," not proceed to schedule a retry regardless.
        """
        # .returning() + checking whether a row came back, not .rowcount -
        # AsyncSession.execute()'s own static return type doesn't narrow
        # to something mypy sees a rowcount attribute on, and this reads
        # more directly as "did the reservation take" anyway.
        result = await self._session.execute(
            sa_update(WorkflowRun)
            .where(WorkflowRun.id == run_id, WorkflowRun.retry_count + cost <= ceiling)
            .values(retry_count=WorkflowRun.retry_count + cost)
            .returning(WorkflowRun.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

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
            payload=workflow_task_completed_payload(
                task.workflow_run_id, task_id, task.task_type
            ),
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
