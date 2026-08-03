import asyncio
import logging
from datetime import UTC, datetime, timedelta

from redis import Redis
from rq import Queue
from tenderos_backend.db.unit_of_work import UnitOfWork

from tenderos_worker.jobs.placeholder import process_outbox_event
from tenderos_worker.settings import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 5
QUEUE_NAME = "maintenance"


def _backoff_seconds(attempt_count: int) -> float:
    """Exponential backoff per Build Guide Part I4 ("transient network/provider
    errors: exponential backoff, max 3")."""
    return float(2**attempt_count)


async def _dispatch_once() -> None:
    connection = Redis.from_url(settings.redis_url)
    queue = Queue(QUEUE_NAME, connection=connection)

    async with UnitOfWork() as uow:
        pending = await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)
        for event in pending:
            enqueue_succeeded = True
            try:
                queue.enqueue(
                    process_outbox_event,
                    str(event.id),
                    event.aggregate_type,
                    event.event_type,
                    # RQ's job_id only allows letters/numbers/underscores/dashes -
                    # no colons, unlike Part I3's own job-ID examples (e.g.
                    # "ingest:{document_id}:{parser_version}"). Same identifying
                    # info, different delimiter - a library-constraint fix, not
                    # a contract change (Part P).
                    job_id=f"outbox-{event.id}",
                )
            except Exception:
                logger.exception("Failed to enqueue outbox event %s", event.id)
                enqueue_succeeded = False

            if enqueue_succeeded:
                await uow.outbox.mark_dispatched(event.id)
            else:
                delay_seconds = _backoff_seconds(event.attempt_count)
                available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
                await uow.outbox.mark_retry(event.id, available_at)
            await uow.commit()


async def _run_forever() -> None:
    # One event loop for the whole process lifetime - asyncpg connections
    # (held open by the module-level SQLAlchemy engine) are bound to whichever
    # event loop was active when they were created, so calling asyncio.run()
    # repeatedly (a fresh loop each time) breaks them on the second iteration.
    while True:
        await _dispatch_once()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
