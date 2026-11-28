"""RQ's workflow queue, its jobs put where a test needs them (plan items 19
and 124): the run reaper's tests and the reanalysis claim reaper's tests
build the same registry states, so they share this.

Moved unchanged from test_run_reaper.py (plan item 124). Real RQ on the test
Redis: no worker runs in the test process, so a job put somewhere stays
there. Built the way item 44's tests built them (test_async_operations.py):
Execution.create for a live started entry, a past score for a dead one,
enqueue_in for scheduled, the failed and finished registries with the job's
status set, job.cancel().
"""

import time
import uuid
from datetime import timedelta

from redis import Redis
from rq import Queue
from rq.executions import Execution
from rq.job import JobStatus
from tenderos_worker.jobs.workflow import retry_workflow_run_job
from tenderos_worker.settings import settings


class RqRegistry:
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


class Clock:
    """The inspector's clock, moved forward by hand for the second look."""

    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now
