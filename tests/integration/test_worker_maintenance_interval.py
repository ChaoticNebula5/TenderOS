"""PR 33: the worker main() runs restarts a dead RQ scheduler at a maintenance
pass every 60 s, not RQ's default 600 s (after a Redis outage, observed 6.5
minutes and up to about 17 by RQ 2.12's code without promoting a workflow
retry), and keeps its worker_ttl. Built on the test Redis (RQ's Worker
talks to Redis when it is constructed), never started."""

from redis import Redis
from rq import Queue
from rq.defaults import DEFAULT_MAINTENANCE_TASK_INTERVAL
from tenderos_worker import worker as worker_module
from tenderos_worker.settings import settings


def test_the_worker_runs_maintenance_every_60_seconds_with_its_worker_ttl():
    connection = Redis.from_url(settings.redis_url)
    try:
        worker = worker_module.build_worker(
            [Queue("pr33-maintenance", connection=connection)], connection
        )

        assert worker.maintenance_interval == worker_module.MAINTENANCE_INTERVAL_SECONDS == 60
        assert DEFAULT_MAINTENANCE_TASK_INTERVAL == 600  # what it replaces
        assert worker.worker_ttl == worker_module.WORKER_TTL
        assert isinstance(worker, worker_module.LogContextWorker)
    finally:
        connection.close()
