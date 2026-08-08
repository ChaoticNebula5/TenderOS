import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from rq import Queue
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_DOCUMENT,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_WORKFLOW_RUN_CREATED,
)

from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document
from tenderos_worker.jobs.placeholder import process_outbox_event
from tenderos_worker.jobs.workflow import resume_workflow, run_workflow
from tenderos_worker.settings import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 5
DEFAULT_QUEUE_NAME = "maintenance"


def _backoff_seconds(attempt_count: int) -> float:
    """Exponential backoff per Build Guide Part I4 ("transient network/provider
    errors: exponential backoff, max 3")."""
    return float(2**attempt_count)


def _default_dispatch(event: OutboxEvent) -> tuple[str, Callable[..., Any], tuple, str]:
    return (
        DEFAULT_QUEUE_NAME,
        process_outbox_event,
        (str(event.id), event.aggregate_type, event.event_type),
        f"outbox-{event.id}",
    )


def _workflow_run_created_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    workflow_run_id = event.payload["workflow_run_id"]
    return "workflow", run_workflow, (workflow_run_id,), f"workflow-{workflow_run_id}"


def _gate_decision_recorded_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    workflow_run_id = event.payload["workflow_run_id"]
    gate_id = event.payload["gate_id"]
    return (
        "workflow",
        resume_workflow,
        (workflow_run_id, gate_id),
        # Underscore, not the dash B2 used for outbox-{event.id} - that job_id
        # has exactly one UUID, but this one has two, and UUIDs themselves
        # contain hyphens, so dash-joining two of them would be ambiguous to
        # split back apart (RQ job_ids only allow [A-Za-z0-9_-], no colons,
        # per B2's earlier RQ constraint discovery - I3's own examples use
        # colons, which is invalid either way).
        f"resume_{workflow_run_id}_{gate_id}",
    )


def _document_uploaded_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    document_id = event.payload["document_id"]
    return (
        "ingestion",
        parse_and_embed_document,
        (document_id,),
        # I3's example is ingest:{document_id}:{parser_version}; underscores
        # for the same reason as resume_ above (RQ rejects colons in job_ids).
        f"ingest_{document_id}_{PARSER_VERSION}",
    )


# (aggregate_type, event_type) -> a function building (queue_name, job_fn,
# job_args, job_id) for that event. Anything not listed here falls back to
# _default_dispatch (maintenance queue, generic process_outbox_event) - same
# "no real consumer yet" placeholder every other event type still uses.
_ROUTES: dict[
    tuple[str, str], Callable[[OutboxEvent], tuple[str, Callable[..., Any], tuple, str]]
] = {
    (AGGREGATE_WORKFLOW_RUN, EVENT_WORKFLOW_RUN_CREATED): _workflow_run_created_dispatch,
    (AGGREGATE_APPROVAL_GATE, EVENT_GATE_DECISION_RECORDED): _gate_decision_recorded_dispatch,
    (AGGREGATE_DOCUMENT, EVENT_DOCUMENT_UPLOADED): _document_uploaded_dispatch,
}


async def _dispatch_once() -> None:
    connection = Redis.from_url(settings.redis_url)
    queues = {
        name: Queue(name, connection=connection)
        for name in (DEFAULT_QUEUE_NAME, "workflow", "ingestion")
    }

    async with UnitOfWork() as uow:
        pending = await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)
        for event in pending:
            build_dispatch = _ROUTES.get(
                (event.aggregate_type, event.event_type), _default_dispatch
            )
            queue_name, job_fn, job_args, job_id = build_dispatch(event)

            enqueue_succeeded = True
            try:
                queues[queue_name].enqueue(job_fn, *job_args, job_id=job_id)
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
