from redis import Redis
from rq import Queue, Worker
from tenderos_backend.core.log_redaction import configure_logging

from tenderos_worker.settings import settings

# Queue names per Build Guide Part B §R1 (Long-Running Execution Model).
QUEUE_NAMES = ["ingestion", "workflow", "projection", "evaluation", "maintenance"]


def main() -> None:
    configure_logging()
    connection = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=connection) for name in QUEUE_NAMES]
    worker = Worker(queues, connection=connection)
    # with_scheduler=True: B16 Group 1's retry mechanism (jobs/workflow.py's
    # _handle_graph_failure) schedules retries via Queue.enqueue_in, which
    # only ever lands a job in RQ's own scheduled-jobs registry - nothing
    # promotes it to a real queue for a worker to pick up unless a
    # scheduler component is running. RQ's own Worker can run one inline
    # (a background thread) with this flag; without it, every scheduled
    # retry would sit invisibly in Redis forever, never executing.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
