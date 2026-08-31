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
    worker.work()


if __name__ == "__main__":
    main()
