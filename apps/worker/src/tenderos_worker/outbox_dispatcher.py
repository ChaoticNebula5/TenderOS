import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from redis import Redis
from rq import Queue
from tenderos_backend.agents.llm.fake_fallbacks import install_fake_fallback_responses
from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    AGGREGATE_REQUIREMENT,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    EVENT_WORKFLOW_RUN_CREATED,
)

from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document
from tenderos_worker.jobs.knowledge_import import process_knowledge_document_import
from tenderos_worker.jobs.placeholder import process_outbox_event
from tenderos_worker.jobs.reanalysis import reanalyze_knowledge_selection_change
from tenderos_worker.jobs.verification import verify_requirement_job
from tenderos_worker.jobs.workflow import resume_workflow_job, run_workflow_job
from tenderos_worker.settings import settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 5
DEFAULT_QUEUE_NAME = "maintenance"

# RQ's own default job_timeout (180s) is sized for short deterministic work
# (parsing, dispatch) - B5's run_workflow/resume_workflow can now include a
# real, sequential, multi-call LLM pipeline (N extraction batches + one
# verification call per mandatory requirement found), which genuinely runs
# past 180s on real network latency, not because anything is stuck - caught
# live: B5's own Docker smoke test hit rq.timeouts.JobTimeoutException on a
# single-document run. Ingestion also gets a generous bump since embedding a
# large document is the same "real work takes real time" shape. Anything not
# listed here keeps RQ's default.
_QUEUE_TIMEOUTS_SECONDS: dict[str, int] = {
    "workflow": 1800,
    "ingestion": 600,
}


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
    # *_job (jobs/workflow.py), not the bare async run_workflow/
    # resume_workflow - see _run_sync's own docstring for why: RQ owns the
    # sync-to-async transition for a directly-enqueued async callable, which
    # left run_workflow's own except block unreachable when RQ's death-
    # penalty timeout fires. The *_job wrappers own that transition instead.
    workflow_run_id = event.payload["workflow_run_id"]
    return "workflow", run_workflow_job, (workflow_run_id,), f"workflow-{workflow_run_id}"


def _gate_decision_recorded_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    workflow_run_id = event.payload["workflow_run_id"]
    gate_id = event.payload["gate_id"]
    return (
        "workflow",
        resume_workflow_job,
        (workflow_run_id, gate_id),
        # Underscore, not the dash B2 used for outbox-{event.id} - that job_id
        # has exactly one UUID, but this one has two, and UUIDs themselves
        # contain hyphens, so dash-joining two of them would be ambiguous to
        # split back apart (RQ job_ids only allow [A-Za-z0-9_-], no colons,
        # per B2's earlier RQ constraint discovery - I3's own examples use
        # colons, which is invalid either way).
        f"resume_{workflow_run_id}_{gate_id}",
    )


def _requirement_verification_requested_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    requirement_id = event.payload["requirement_id"]
    return (
        # No dedicated queue exists for this in Part B's locked 5-queue
        # table (ingestion/workflow/projection/evaluation/maintenance) -
        # "workflow" is the closest fit (on-demand agent work triggered by a
        # human action on a tender's workflow, same category as
        # resume_workflow), reused rather than adding a 6th queue for one
        # job type. Flagged here for the record.
        "workflow",
        verify_requirement_job,
        (requirement_id,),
        f"verify_{requirement_id}_{event.id}",
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


def _knowledge_document_uploaded_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    document_id = event.payload["document_id"]
    batch_id = event.payload["batch_id"]
    return (
        # "workflow", not "ingestion" - unlike plain DocumentUploaded, this
        # job can make LLM calls (resume/capability-statement extraction),
        # the same multi-call-real-latency shape run_workflow/
        # resume_workflow already justify the 1800s timeout for.
        "workflow",
        process_knowledge_document_import,
        (document_id, batch_id),
        f"knowledge_import_{document_id}",
    )


def _knowledge_selection_changed_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    tender_id = event.payload["tender_id"]
    knowledge_item_id = event.payload["knowledge_item_id"]
    state_version = event.payload["state_version"]
    return (
        # "workflow", not a new "projection"/"evaluation" queue - this job's
        # own shape (a Neo4j reprojection, then a real evaluate_compliance_
        # for_requirement/reground_claim loop making LLM calls) matches
        # _knowledge_document_uploaded_dispatch's own reasoning, not
        # ingestion's parse-only one.
        "workflow",
        reanalyze_knowledge_selection_change,
        (tender_id, knowledge_item_id, state_version),
        # I3's own example is project:{tender_id}:{state_version}; reused
        # here (underscores, not colons, for the same RQ job_id constraint
        # every other dispatch function in this file already works around)
        # since this job's first step is a reprojection at exactly that
        # state_version. Keyed on state_version, not knowledge_item_id -
        # two different items deselected in quick succession are two
        # different state_versions and so two different jobs, matching the
        # outbox event's own idempotency_key (both derived from the same
        # tender mutation - see outbox_events.py).
        f"reanalyze_{tender_id}_{state_version}",
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
    (
        AGGREGATE_DOCUMENT,
        EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    ): _knowledge_document_uploaded_dispatch,
    (
        AGGREGATE_REQUIREMENT,
        EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    ): _requirement_verification_requested_dispatch,
    (
        AGGREGATE_KNOWLEDGE_SELECTION,
        EVENT_KNOWLEDGE_SELECTION_CHANGED,
    ): _knowledge_selection_changed_dispatch,
}


async def _dispatch_claimed_event(queues: dict[str, Queue], event_id: uuid.UUID) -> None:
    """Claims (by id) and processes one candidate event inside one
    transaction - major finding #22's real fix. `claim_by_id`'s own
    `FOR UPDATE SKIP LOCKED` holds a real Postgres row lock for the whole
    claim-enqueue-mark-commit window, so a second, concurrent dispatcher
    instance's own call for this SAME id skips it (gets None back)
    instead of also reading and double-dispatching it. A None claim here
    is a real, silent no-op, not an error - it means either another
    dispatcher already claimed this exact row, or it no longer matches
    (already dispatched, or its own backoff hasn't elapsed yet) by the
    time this call reached it.
    """
    async with UnitOfWork() as uow:
        event = await uow.outbox.claim_by_id(event_id, max_attempts=MAX_ATTEMPTS)
        if event is None:
            return

        enqueue_succeeded = True
        try:
            # build_dispatch(event) belongs inside this try, not before it -
            # every route builder does a raw event.payload["..."] lookup
            # (e.g. _workflow_run_created_dispatch), which raises a bare
            # KeyError on a malformed payload. Outside this block, that
            # KeyError isn't caught by anything: it escapes this function,
            # aborts _dispatch_once's own candidate_ids loop (every event
            # after this one in the same poll cycle never gets attempted),
            # and _run_forever has no try/except of its own around
            # _dispatch_once() either - it would take the whole dispatcher
            # process down. Worse, since the exception happens before
            # mark_dispatched/mark_retry ever run, this event's own
            # attempt_count never increments, so get_pending selects it
            # again on every future poll - a single malformed payload
            # would otherwise crash the dispatcher forever, not once.
            build_dispatch = _ROUTES.get(
                (event.aggregate_type, event.event_type), _default_dispatch
            )
            queue_name, job_fn, job_args, job_id = build_dispatch(event)
            queues[queue_name].enqueue(
                job_fn,
                *job_args,
                job_id=job_id,
                job_timeout=_QUEUE_TIMEOUTS_SECONDS.get(queue_name),
            )
        except Exception:
            logger.exception("Failed to dispatch outbox event %s", event.id)
            enqueue_succeeded = False

        if enqueue_succeeded:
            await uow.outbox.mark_dispatched(event.id)
        else:
            delay_seconds = _backoff_seconds(event.attempt_count)
            available_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            await uow.outbox.mark_retry(event.id, available_at)
        await uow.commit()


async def _dispatch_once() -> None:
    connection = Redis.from_url(settings.redis_url)
    queues = {
        name: Queue(name, connection=connection)
        for name in (DEFAULT_QUEUE_NAME, "workflow", "ingestion")
    }

    # A FIXED candidate list, read once (get_pending's own plain read, no
    # lock needed here - the real protection happens per-event, below).
    # Each event gets at most ONE claim attempt this call, deliberately -
    # see _dispatch_claimed_event's own docstring for why a "keep
    # claiming whatever's next" loop (an earlier version of this fix)
    # could instead re-attempt the SAME just-failed event several times
    # within one _dispatch_once() call, once its own short backoff
    # elapsed mid-loop.
    async with UnitOfWork() as uow:
        candidate_ids = [e.id for e in await uow.outbox.get_pending(max_attempts=MAX_ATTEMPTS)]

    for event_id in candidate_ids:
        await _dispatch_claimed_event(queues, event_id)


async def _run_forever() -> None:
    # One event loop for the whole process lifetime - asyncpg connections
    # (held open by the module-level SQLAlchemy engine) are bound to whichever
    # event loop was active when they were created, so calling asyncio.run()
    # repeatedly (a fresh loop each time) breaks them on the second iteration.
    while True:
        await _dispatch_once()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    configure_logging()
    # B17: a real no-op unless LLM_PROVIDER=fake - see that function's own
    # docstring. This process never calls an LLM itself, but installing it
    # here too keeps all three real entrypoints identical, matching
    # configure_logging()'s own placement.
    install_fake_fallback_responses()
    asyncio.run(_run_forever())


if __name__ == "__main__":
    main()
