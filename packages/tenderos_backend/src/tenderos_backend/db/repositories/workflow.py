import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.workflow import (
    WorkflowRun,
    WorkflowTask,
    WorkflowTaskDependency,
)


class WorkflowRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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

    async def mark_task_failed(self, task_id: uuid.UUID, error_code: str) -> None:
        task = await self._session.get(WorkflowTask, task_id)
        if task is None:
            raise ValueError(f"WorkflowTask {task_id} not found")
        task.status = "failed"
        task.error_code = error_code
        task.attempt_count += 1
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
