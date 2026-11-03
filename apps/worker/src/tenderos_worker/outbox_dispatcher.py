import asyncio
import contextlib
import logging
import random
import signal
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.retry import Retry
from rq import Queue
from rq.exceptions import DuplicateJobError
from sqlalchemy import text
from tenderos_backend.agents.llm.fake_fallbacks import install_fake_fallback_responses
from tenderos_backend.core.log_redaction import configure_logging
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.session import engine
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    AGGREGATE_ASYNC_OPERATION,
    AGGREGATE_DOCUMENT,
    AGGREGATE_KNOWLEDGE_SELECTION,
    AGGREGATE_REQUIREMENT,
    AGGREGATE_WORKFLOW_RUN,
    EVENT_DOCUMENT_UPLOADED,
    EVENT_GATE_DECISION_RECORDED,
    EVENT_KNOWLEDGE_DOCUMENT_UPLOADED,
    EVENT_KNOWLEDGE_SELECTION_CHANGED,
    EVENT_OPERATION_REQUESTED,
    EVENT_REQUIREMENT_VERIFICATION_REQUESTED,
    EVENT_WORKFLOW_RUN_CREATED,
)
from tenderos_backend.domain.services import operation_service

from tenderos_worker.jobs.ingestion import PARSER_VERSION, parse_and_embed_document
from tenderos_worker.jobs.knowledge_import import process_knowledge_document_import
from tenderos_worker.jobs.operations import run_operation_job
from tenderos_worker.jobs.placeholder import process_outbox_event
from tenderos_worker.jobs.reanalysis import reanalyze_knowledge_selection_change
from tenderos_worker.jobs.verification import verify_requirement_job
from tenderos_worker.jobs.workflow import (
    WORKFLOW_JOB_TIMEOUT_SECONDS,
    resume_workflow_job,
    run_workflow_job,
)
from tenderos_worker.settings import settings
from tenderos_worker.worker import OPERATIONS_QUEUE

logger = logging.getLogger(__name__)

# Phase 4 blocker 3 retry policy. Design numbers, not measured.
#
# Only errors that are not about Redis being reachable count as attempts
# (see INFRASTRUCTURE_ERRORS below for the ones that do not). A counted
# failure waits min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2^(n-1)) after
# its n-th failed attempt, scaled by a random factor in
# [RETRY_JITTER_LOW, 1.0] so events that failed together do not retry in
# lockstep, and never above the cap. The 7 waits between 8 attempts are
# nominally 30, 60, 120, 240, 300, 300 and 300 s: 22.5 minutes in all,
# about 17 to 22.5 with jitter. The 8th failure marks the event failed
# (the dead-letter state) and logs it at ERROR.
#
# This replaces "1, 2 and 4 s, 3 attempts", which a live 22 s Redis outage
# showed to be too short: every event written during the outage used up
# its three attempts and was never delivered (KNOWN_LIMITATIONS.md, Phase 4
# blocker 3). It also departs from the earlier "max 3" reading of the
# retry rule, deliberately.
MAX_ATTEMPTS = 8
RETRY_BASE_SECONDS = 30.0
RETRY_CAP_SECONDS = 300.0
RETRY_JITTER_LOW = 0.75

# Errors that mean "Redis cannot be reached right now", not "this event is
# bad". redis-py 8.0.1 raises ConnectionError for a refused connection, a
# DNS failure (a stopped Redis container's hostname no longer resolves; the
# live outage check logged "Error -2 connecting to redis:6379. Name or
# service not known") and a connection closed by the server, and
# TimeoutError for a socket timeout (redis/exceptions.py). ConnectionError's
# subclasses come along: BusyLoadingError (Redis still loading its dataset)
# is transient; AuthenticationError and AuthorizationError are
# misconfiguration, and holding events until it is fixed is better than
# burning their attempts. None of these consumes an attempt: the event stays
# pending with available_at unchanged, the rest of the tick's batch is
# skipped, and the tick backoff in _run_forever paces the retries.
INFRASTRUCTURE_ERRORS = (RedisConnectionError, RedisTimeoutError)

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
    "workflow": WORKFLOW_JOB_TIMEOUT_SECONDS,
    "ingestion": 600,
}

# Phase 4 blocker 3. Design numbers, not measured.
#
# Redis: an enqueue is a handful of small commands (script load, the unique
# enqueue Lua script, a set add), each well under a millisecond on a healthy
# local Redis. 2 s each tolerates a Redis that is briefly paused (a fork for
# a background save) without calling it down, and bounds a hung connection,
# which redis-py 8.0.1 would otherwise wait on for its 5 s default per
# command (redis/_defaults.py). Retries pinned to zero for the same reason
# as rate_limit.py: a failed enqueue is retried by the outbox itself
# (mark_retry), so a client-side retry ladder only stretches the time the
# claim's row lock is held.
REDIS_SOCKET_TIMEOUT_SECONDS = 2.0
REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS = 2.0

# Backstops for the claiming transaction, which holds the event's row lock
# across the enqueue (option A in KNOWN_LIMITATIONS.md, releasing the lock
# first, is designed but not built). Both are well above the worst case one
# enqueue can take with the timeouts above (about 4 commands x 2 s plus a
# connect per address family, roughly 12 s, an estimate), so they only fire
# if something other than Redis stalls the transaction. On expiry Postgres
# ends the transaction and releases the lock; the tick fails and the loop
# below logs it and carries on.
CLAIM_LOCK_TIMEOUT = "30s"
CLAIM_IDLE_IN_TRANSACTION_TIMEOUT = "30s"

# A failed tick (Postgres unreachable, a bug) waits POLL_INTERVAL_SECONDS,
# doubling per consecutive failure up to this cap, then retries; one good
# tick resets it.
MAX_ERROR_BACKOFF_SECONDS = 60.0

# Idempotency-record cleanup (IdempotencyRepository.delete_expired), run
# from this loop because it is the one long-running process with a regular
# tick. Every 12 ticks is about once a minute at the 5 s poll interval; 500
# rows per batch is up to 720,000 rows a day, far above what seven
# idempotent endpoints produce (an estimate, not measured).
CLEANUP_EVERY_N_TICKS = 12
CLEANUP_BATCH_SIZE = 500

# Async operations whose worker lease lapsed are failed as WORKER_LOST on the
# same cadence (about once a minute), at most this many per pass. Leases are
# 11 to 31 minutes (operation_service.lease_for), so a minute's delay in
# noticing one is small next to the lease itself.
REAPER_BATCH_SIZE = 100


class RedisUnavailableError(Exception):
    """Raised out of a tick when an enqueue hit one of INFRASTRUCTURE_ERRORS.
    The event's claim is rolled back untouched and the rest of the batch is
    skipped: if Redis is unreachable for one row it is for the next, and
    each row would otherwise cost a full socket timeout."""


def _retry_delay_seconds(failed_attempts: int) -> float:
    """The wait after an event's `failed_attempts`-th counted failure (1 for
    the first). See MAX_ATTEMPTS for the policy and the numbers."""
    nominal = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** (failed_attempts - 1))
    return nominal * random.uniform(RETRY_JITTER_LOW, 1.0)


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
        # The event id lets the job skip a duplicate delivery (Phase 4
        # consumer guard, jobs/verification.py).
        (requirement_id, str(event.id)),
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
        # Phase 4: the event id is part of the job id. A deliberate
        # reprocess emits a new DocumentUploaded for the same document;
        # with unique=True and an id shared across events, a reprocess
        # within RQ's 500 s result TTL of the last ingest was treated as
        # already dispatched and never ran. Redeliveries of one event still
        # share an id; re-runs across events are the consumer guard's job.
        f"ingest_{document_id}_{PARSER_VERSION}_{event.id}",
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
        # The event id is part of the job id: one document can have several
        # KnowledgeDocumentUploaded events (replace_employee_resume re-queues
        # an existing document under a fresh request id,
        # knowledge_service.py), and with unique=True a shared id made every
        # later import silently "already dispatched" while the earlier
        # job's hash existed (500 s after it finished, a year after it
        # failed).
        f"knowledge_import_{document_id}_{event.id}",
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


def _operation_requested_dispatch(
    event: OutboxEvent,
) -> tuple[str, Callable[..., Any], tuple, str]:
    # Phase 4 async operations: one job per operation, whose id is part of
    # the job id (op_{operation_id}); OperationRequested is written exactly
    # once per operation (operation_requested_idempotency_key). Its own
    # "operations" queue (Stage 2), served by its own worker process, so an
    # operation a person is waiting on never queues behind a workflow job
    # (worker.OPERATIONS_QUEUE). Its job timeout comes from the operation's
    # kind (_job_timeout_for).
    operation_id = event.payload["operation_id"]
    return OPERATIONS_QUEUE, run_operation_job, (operation_id,), f"op_{operation_id}"


def _job_timeout_for(event: OutboxEvent, queue_name: str) -> int | None:
    """The RQ job timeout for this event's job. An async operation's comes
    from its kind (operation_service.JOB_TIMEOUT_SECONDS), the same number
    its lease is built from, so the lease always outlives the job. Every
    other job keeps its queue's timeout."""
    if (event.aggregate_type, event.event_type) == (
        AGGREGATE_ASYNC_OPERATION,
        EVENT_OPERATION_REQUESTED,
    ):
        kind = event.payload.get("kind")
        if kind in operation_service.JOB_TIMEOUT_SECONDS:
            return operation_service.JOB_TIMEOUT_SECONDS[kind]
    return _QUEUE_TIMEOUTS_SECONDS.get(queue_name)


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
    (AGGREGATE_ASYNC_OPERATION, EVENT_OPERATION_REQUESTED): _operation_requested_dispatch,
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

    Phase 4 blocker 3:
    - The enqueue runs in a worker thread (asyncio.to_thread): redis-py's
      client is synchronous, and calling it on the event loop froze the
      whole dispatcher for as long as Redis took to answer.
    - It is enqueued with unique=True. RQ then refuses a job id whose job
      hash already exists in Redis (rq 2.12.0, the same lines as in 2.10.0:
      the Lua check in rq/scripts.py:22, raised as DuplicateJobError at
      :96-97): a job that is queued,
      started, deferred or scheduled, a finished job until its result TTL
      lapses (500 s by default, rq/defaults.py:43), or a failed one until
      its failure TTL lapses (1 year, :50). Every job id is derived from a
      single event, so an existing job means this event was already
      enqueued, for example by an attempt that enqueued and then crashed or
      timed out before marking the row; it is marked dispatched, not
      enqueued again. Once the hash has expired, a redelivery does enqueue
      again, and the consumer's own guard (run_workflow's claim) is what
      makes that safe.
    - SET LOCAL lock_timeout and idle_in_transaction_session_timeout bound
      how long this transaction can hold the row lock (see the constants).
    """
    async with UnitOfWork() as uow:
        await uow.session.execute(text(f"SET LOCAL lock_timeout = '{CLAIM_LOCK_TIMEOUT}'"))
        await uow.session.execute(
            text(
                "SET LOCAL idle_in_transaction_session_timeout = "
                f"'{CLAIM_IDLE_IN_TRANSACTION_TIMEOUT}'"
            )
        )
        event = await uow.outbox.claim_by_id(event_id, max_attempts=MAX_ATTEMPTS)
        if event is None:
            return

        enqueue_succeeded = True
        job_id = None
        try:
            # build_dispatch(event) belongs inside this try, not before it -
            # every route builder does a raw event.payload["..."] lookup
            # (e.g. _workflow_run_created_dispatch), which raises a bare
            # KeyError on a malformed payload. Outside this block, that
            # KeyError isn't caught by anything: it escapes this function,
            # aborts _dispatch_once's own candidate_ids loop (every event
            # after this one in the same poll cycle never gets attempted),
            # and fails the whole tick. Worse, since the exception happens
            # before mark_dispatched/mark_retry ever run, this event's own
            # attempt_count never increments, so get_pending selects it
            # again on every future poll - a single malformed payload
            # would otherwise fail every tick forever, not once.
            build_dispatch = _ROUTES.get(
                (event.aggregate_type, event.event_type), _default_dispatch
            )
            queue_name, job_fn, job_args, job_id = build_dispatch(event)
            await asyncio.to_thread(
                queues[queue_name].enqueue,
                job_fn,
                *job_args,
                job_id=job_id,
                job_timeout=_job_timeout_for(event, queue_name),
                unique=True,
            )
        except DuplicateJobError:
            logger.info(
                "outbox event %s: job %s already exists in Redis; treating it as dispatched",
                event_id,
                job_id,
            )
        except INFRASTRUCTURE_ERRORS as exc:
            # Leaving this block by raising rolls the claim back: the row is
            # unchanged (still pending, same attempt_count and available_at)
            # and its lock is released.
            raise RedisUnavailableError(
                f"enqueue for outbox event {event_id} failed: {type(exc).__name__}"
            ) from exc
        except Exception:
            logger.exception("Failed to dispatch outbox event %s", event_id)
            enqueue_succeeded = False

        if enqueue_succeeded:
            await uow.outbox.mark_dispatched(event_id)
        else:
            failed_attempts = event.attempt_count + 1
            if failed_attempts >= MAX_ATTEMPTS:
                await uow.outbox.mark_failed(event_id)
                await _fail_dead_lettered_operation(uow, event)
                logger.error(
                    "outbox event %s (%s/%s) failed %d attempts; marked failed "
                    "(dead letter, needs a manual re-drive)",
                    event_id,
                    event.aggregate_type,
                    event.event_type,
                    failed_attempts,
                )
            else:
                delay = timedelta(seconds=_retry_delay_seconds(failed_attempts))
                await uow.outbox.mark_retry(event_id, delay)
        await uow.commit()


async def _fail_dead_lettered_operation(uow: UnitOfWork, event: OutboxEvent) -> None:
    """A dead-lettered OperationRequested never reaches the worker, so its
    operation would stay queued, and queued counts as active in
    uq_async_operation_active: every later request for that kind on that
    tender would get the stuck operation back. Fail it here, in the
    dead-letter's own transaction. A payload without a readable
    operation_id is left to the queued-window reaper rather than raising,
    which would roll back the dead-letter and retry the event forever."""
    if (event.aggregate_type, event.event_type) != (
        AGGREGATE_ASYNC_OPERATION,
        EVENT_OPERATION_REQUESTED,
    ):
        return
    try:
        operation_id = uuid.UUID(str(event.payload["operation_id"]))
    except (KeyError, TypeError, ValueError):
        logger.error(
            "outbox event %s: dead-lettered OperationRequested has no readable operation_id; "
            "the queued-window reaper will fail its operation",
            event.id,
        )
        return
    if await operation_service.fail_undispatched_operation(uow, operation_id):
        logger.error(
            "operation %s failed as DISPATCH_FAILED: its request event %s was dead-lettered",
            operation_id,
            event.id,
        )


def _redis_connection(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
        socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS,
        retry=Retry(NoBackoff(), 0),
    )


def _queues(connection: Redis) -> dict[str, Queue]:
    return {
        name: Queue(name, connection=connection)
        for name in (DEFAULT_QUEUE_NAME, "workflow", "ingestion", OPERATIONS_QUEUE)
    }


async def _dispatch_once(queues: dict[str, Queue] | None = None) -> None:
    """One poll: dispatch every currently eligible event once.

    The long-running loop passes the queues it built once for the whole
    process. Called without them (tests, one-off use), this builds a
    connection from settings.redis_url and closes it afterwards.
    """
    connection = None
    if queues is None:
        connection = _redis_connection(settings.redis_url)
        queues = _queues(connection)
    try:
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
    finally:
        if connection is not None:
            connection.close()


async def _delete_expired_idempotency_records() -> None:
    """One bounded cleanup batch in its own short transaction. A failure
    is logged and swallowed: cleanup must never stop dispatching."""
    try:
        async with UnitOfWork() as uow:
            deleted = await uow.idempotency.delete_expired(batch_size=CLEANUP_BATCH_SIZE)
            await uow.commit()
        if deleted:
            logger.info("deleted %d expired idempotency records", deleted)
    except Exception:
        logger.exception("idempotency record cleanup failed; will retry on a later tick")


async def _delete_expired_refresh_tokens() -> None:
    """Plan item 34, option 3: one bounded batch of expired refresh-token
    rows, its own short transaction, on the same cadence and batch size as
    the idempotency cleanup. One row per login plus one per refresh is
    about 33 a day for an active 8-hour session at 15-minute access tokens
    (one login and 32 refreshes; an estimate). A failure is logged and
    swallowed, and does not skip the other cleanups."""
    try:
        async with UnitOfWork() as uow:
            deleted = await uow.refresh_tokens.delete_expired(batch_size=CLEANUP_BATCH_SIZE)
            await uow.commit()
        if deleted:
            logger.info("deleted %d expired refresh tokens", deleted)
    except Exception:
        logger.exception("refresh token cleanup failed; will retry on a later tick")


async def _reap_lapsed_operations() -> None:
    """Fail async operations whose worker lease lapsed (WORKER_LOST), then
    those still queued past operation_service.MAX_DISPATCH_WINDOW
    (DISPATCH_FAILED), one bounded batch of each per call, each in its own
    transaction. Never re-runs them: that would repeat paid LLM calls; the
    user starts the work again. A failure in either is logged and
    swallowed, like the idempotency cleanup, and does not skip the other."""
    try:
        reaped = await operation_service.reap_lapsed_operations(batch_size=REAPER_BATCH_SIZE)
        if reaped:
            logger.warning(
                "reaped %d async operation(s) whose worker lease lapsed: %s",
                len(reaped),
                ", ".join(str(r) for r in reaped),
            )
    except Exception:
        logger.exception("async operation reaper failed; will retry on a later tick")
    try:
        stale = await operation_service.reap_stale_queued_operations(batch_size=REAPER_BATCH_SIZE)
        if stale:
            logger.warning(
                "failed %d async operation(s) still queued past the dispatch window: %s",
                len(stale),
                ", ".join(str(s) for s in stale),
            )
    except Exception:
        logger.exception("queued async operation reaper failed; will retry on a later tick")


def _error_backoff_seconds(consecutive_failures: int) -> float:
    return min(POLL_INTERVAL_SECONDS * 2 ** (consecutive_failures - 1), MAX_ERROR_BACKOFF_SECONDS)


async def _run_forever(stop: asyncio.Event | None = None) -> None:
    # One event loop for the whole process lifetime - asyncpg connections
    # (held open by the module-level SQLAlchemy engine) are bound to whichever
    # event loop was active when they were created, so calling asyncio.run()
    # repeatedly (a fresh loop each time) breaks them on the second iteration.
    #
    # Phase 4 blocker 3: one Redis connection for the whole process, and a
    # tick that fails (Postgres unreachable, an unexpected bug) is logged
    # with its traceback and retried after a growing backoff instead of
    # ending the process. Before, the first exception out of _dispatch_once
    # stopped all event delivery until someone restarted the container.
    #
    # Phase 4 item 28: `stop`, once set, ends the loop after the tick in
    # progress, and cuts the wait between ticks short. Without one (the
    # integration tests), the loop runs until cancelled.
    if stop is None:
        stop = asyncio.Event()
    connection = _redis_connection(settings.redis_url)
    queues = _queues(connection)
    tick = 0
    consecutive_failures = 0
    while not stop.is_set():
        tick += 1
        delay: float = POLL_INTERVAL_SECONDS
        try:
            await _dispatch_once(queues)
            consecutive_failures = 0
        except RedisUnavailableError as exc:
            # Expected during a Redis outage, once per tick: no traceback.
            consecutive_failures += 1
            delay = _error_backoff_seconds(consecutive_failures)
            logger.warning(
                "Redis unavailable (%s; cause %s); event delivery paused, "
                "%d tick(s) in a row, next attempt in %.0f s",
                exc,
                type(exc.__cause__).__name__,
                consecutive_failures,
                delay,
            )
        except Exception:
            consecutive_failures += 1
            delay = _error_backoff_seconds(consecutive_failures)
            logger.exception(
                "outbox dispatcher tick failed (%d in a row); next attempt in %.0f s",
                consecutive_failures,
                delay,
            )
        if tick % CLEANUP_EVERY_N_TICKS == 0:
            await _delete_expired_idempotency_records()
            await _delete_expired_refresh_tokens()
            await _reap_lapsed_operations()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=delay)
    logger.info("outbox dispatcher stopping after tick %d", tick)
    connection.close()


async def _run_until_terminated() -> None:
    """Phase 4 item 28. SIGTERM sets the stop event: the tick in progress
    finishes, and the process exits 0, instead of dying mid-tick. Under
    compose, tini (`init: true`) is PID 1 and forwards the signal; where the
    image runs without an init, the dispatcher is PID 1 itself, and the
    kernel would ignore a SIGTERM it had no handler for. At-least-once delivery
    (decision 0029) makes a kill mid-tick safe too; this only makes the
    ordinary stop quick and clean. Linux only (add_signal_handler), like
    the container."""
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    try:
        await _run_forever(stop)
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    # B17: a real no-op unless LLM_PROVIDER=fake - see that function's own
    # docstring. This process never calls an LLM itself, but installing it
    # here too keeps all three real entrypoints identical, matching
    # configure_logging()'s own placement.
    install_fake_fallback_responses()
    asyncio.run(_run_until_terminated())


if __name__ == "__main__":
    main()
