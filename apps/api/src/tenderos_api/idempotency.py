import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import anyio
from fastapi import Request, Response
from sqlalchemy.exc import IntegrityError
from tenderos_backend.db.models.idempotency import IdempotencyRecord
from tenderos_backend.db.unit_of_work import UnitOfWork

from tenderos_api.dependencies import _resolve_organization_id
from tenderos_api.errors import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyRequiredError,
)

logger = logging.getLogger(__name__)

# Phase 4 blocker 2. How long a request holds its claim on an in_progress
# record before a retry with the same key may take the record over. A design
# number, not measured: it assumes every idempotent request finishes well
# inside 60 s. All seven operations now do their slow work through the
# outbox; proposal_generation was the last to call the LLM inline, and since
# Phase 4 Stage 2 its route only records an async operation and returns 202
# (routes/proposal.py), so the key replays that fast response.
_LEASE = timedelta(seconds=60)

# How long a record is kept at all (expires_at), for replaying a completed
# response. Applied on the database clock; expired rows are removed by
# IdempotencyRepository.delete_expired.
_RECORD_RETENTION = timedelta(hours=24)

# Upper bound on releasing a key after a failure or a cancellation. A design
# number: the release is one DELETE by primary key, and the api's stop
# grace is 20 s (docker-compose.yml) with uvicorn's own graceful timeout at
# 10 s, so 5 s leaves the stop on time.
_RELEASE_TIMEOUT_SECONDS = 5


def request_hash(method: str, path: str, query: str, body: bytes) -> str:
    """The fingerprint that decides whether a retry is "the same request".

    The lookup key is (organization_id, operation, idempotency_key), and
    operation is a fixed literal per route (e.g. "graph_projection_trigger"),
    never the resource id in the path. Two different resources of the same
    type in the same org (two tenders, two gates) hit with the same
    Idempotency-Key would otherwise collide on one record, and the second
    request would silently replay the first's response instead of running
    its own mutation - demonstrated live by
    test_duplicate_idempotency_key_replays_wrong_tenders_graph_projection.
    The method and the resolved path (real ids, not the route template) fold
    the resource identity into the hash, so a same-key request against a
    different resource gets IDEMPOTENCY_CONFLICT, the same answer a
    different body gets. The query string is folded in for the same reason:
    two requests differing only in a query parameter are different requests.

    Public so tests can build a matching record without re-deriving it.
    """
    identity = f"{method}\n{path}\n{query}\n".encode()
    return hashlib.sha256(identity + body).hexdigest()


@dataclass
class IdempotencyContext:
    """What a route gets from the dependency.

    Three shapes: inactive (no Idempotency-Key header on an optional route;
    every method is a no-op), replay (replay_response is set; the route
    returns it and does no work), and claimed (record_id and claim_token are
    set; this request holds the lease and must do the work, then call
    finalize). Only a claimed context can complete or release a record, and
    only while its claim_token is still the record's.
    """

    active: bool
    operation: str | None = None
    record_id: UUID | None = None
    claim_token: UUID | None = None
    replay_response: tuple[int, dict] | None = None

    async def finalize(self, status_code: int, body: dict) -> None:
        if self.record_id is None or self.claim_token is None:
            return
        async with UnitOfWork() as uow:
            stored = await uow.idempotency.complete(
                self.record_id, self.claim_token, status_code, body
            )
            await uow.commit()
        if not stored:
            logger.warning(
                "idempotency %s: claim on record %s was lost before finalize "
                "(lease taken over or record gone); response not stored",
                self.operation,
                self.record_id,
            )

    async def release(self) -> None:
        if self.record_id is None or self.claim_token is None:
            return
        async with UnitOfWork() as uow:
            released = await uow.idempotency.release(self.record_id, self.claim_token)
            await uow.commit()
        if not released:
            logger.warning(
                "idempotency %s: claim on record %s was lost before release "
                "(lease taken over, already completed, or record gone); left as is",
                self.operation,
                self.record_id,
            )


def get_idempotency_context(
    operation: str, required: bool = False
) -> Callable[[Request, Response], AsyncIterator[IdempotencyContext]]:
    """Idempotency-Key was optional-but-honored for every B2 operation: Part I1
    only requires it for create workflow run / gate decision / resource
    selection / knowledge selection change / proposal generation / graph
    projection trigger, none of which existed in B2. B3 introduces the first
    two of those (create_workflow_run, gate_decision) - `required=True` on
    those call sites makes the header actually mandatory (400
    IDEMPOTENCY_KEY_REQUIRED if absent) rather than silently falling back to
    active=False, matching I1's literal text instead of B2's blanket
    optional-everywhere default.

    Phase 4 blocker 2: a yield dependency, so it can release the key when
    the route fails. FastAPI (0.148.0) throws the route's exception into
    this generator at its `yield` (the dependency sits on the request's
    AsyncExitStack), before the error response is built. Any exception, an
    APIError included, deletes this request's record so a retry with the
    same key runs the work again, then the exception is re-raised
    unchanged.

    A cancelled request (client gone, server stopping) releases too:
    asyncio.CancelledError is a BaseException, so it is caught here as
    well. The release runs in a shielded scope. The route runs inside
    BaseHTTPMiddleware's anyio task group, and anyio cancellation is
    level-triggered: without the shield the release's own first await is
    cancelled too (observed, "Cancelled via cancel scope"). The shield is
    bounded by _RELEASE_TIMEOUT_SECONDS so a stopping server cannot hang
    on it; past that, the lease runs out and a retry takes the record over.

    Blocker 2, limit 4: a replay sets the stored status code on the
    response FastAPI builds, through the Response parameter it shares with
    dependencies. A status set there overrides the route's default
    (fastapi/routing.py, serialize_response's caller), so every route that
    returns the replayed body answers with the code first stored.
    """

    async def _dependency(
        request: Request, response: Response
    ) -> AsyncIterator[IdempotencyContext]:
        context = await _claim_or_replay(request, operation, required)
        if context.replay_response is not None:
            response.status_code = context.replay_response[0]
        if context.claim_token is None:
            yield context
            return
        try:
            yield context
        except BaseException:
            await _release_after_failure(context)
            raise

    return _dependency


async def _release_after_failure(context: IdempotencyContext) -> None:
    """Best effort: if the release fails or runs out of time, the lease runs
    out and a retry takes the record over. The route's own exception,
    re-raised by the caller, is the one the client sees."""
    with anyio.move_on_after(_RELEASE_TIMEOUT_SECONDS, shield=True) as scope:
        try:
            await context.release()
        except Exception:
            logger.exception(
                "idempotency %s: could not release record %s",
                context.operation,
                context.record_id,
            )
    if scope.cancelled_caught:
        logger.warning(
            "idempotency %s: release of record %s took over %s s; left for the lease",
            context.operation,
            context.record_id,
            _RELEASE_TIMEOUT_SECONDS,
        )


async def _claim_or_replay(request: Request, operation: str, required: bool) -> IdempotencyContext:
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key is None:
        if required:
            raise IdempotencyKeyRequiredError()
        return IdempotencyContext(active=False)

    organization_id = await _resolve_organization_id(request)
    fingerprint = request_hash(
        request.method, request.url.path, request.url.query, await request.body()
    )

    async with UnitOfWork() as uow:
        existing = await uow.idempotency.get(organization_id, operation, idempotency_key)
        if existing is None:
            # B16: select-then-insert - two genuinely concurrent requests
            # with the same key can both observe "no existing record" here
            # and both attempt to create one; the loser hits
            # uq_idempotency_key, re-reads, and is treated like any request
            # that found the winner's record: with the winner's lease live,
            # that is IDEMPOTENCY_IN_PROGRESS.
            try:
                record = await uow.idempotency.create_in_progress(
                    organization_id=organization_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=fingerprint,
                    lease=_LEASE,
                    retention=_RECORD_RETENTION,
                )
                claimed = _claimed(operation, record)
                await uow.commit()
                return claimed
            except IntegrityError:
                await uow.rollback()
                async with UnitOfWork() as reread_uow:
                    existing = await reread_uow.idempotency.get(
                        organization_id, operation, idempotency_key
                    )
                if existing is None:
                    raise

    return await _context_for_existing_record(existing, fingerprint, operation)


def _claimed(operation: str, record: IdempotencyRecord) -> IdempotencyContext:
    return IdempotencyContext(
        active=True,
        operation=operation,
        record_id=record.id,
        claim_token=record.claim_token,
    )


async def _context_for_existing_record(
    existing: IdempotencyRecord, fingerprint: str, operation: str
) -> IdempotencyContext:
    """A record already exists for this key.

    - Different request (hash mismatch): IDEMPOTENCY_CONFLICT, unchanged.
    - Completed: replay its stored response.
    - In progress with a live lease: IDEMPOTENCY_IN_PROGRESS, Retry-After
      = seconds left on that lease.
    - In progress with a lapsed lease (or locked_until NULL, a row from
      before migration 019): take the record over and do the work.

    History (B16): an earlier version let a second request proceed whenever
    it found a record "not completed", which produced two real tenders for
    one key under genuine concurrency (confirmed with asyncio.gather against
    the real ASGI app). B16 closed that by making "not completed" always
    conflict, which in turn left a crashed request's record in_progress
    forever. The lease is what tells a live first attempt (wait) from a dead
    one (take over).
    """
    if existing.request_hash != fingerprint:
        raise IdempotencyConflictError()

    if existing.status == "completed":
        if existing.response_status is None or existing.response_body is None:
            raise RuntimeError("Completed idempotency record has no saved response")
        return IdempotencyContext(
            active=True,
            operation=operation,
            replay_response=(existing.response_status, existing.response_body),
        )

    if existing.status != "in_progress":
        # 'failed' is allowed by ck_idempotency_status but never written.
        raise IdempotencyConflictError()

    async with UnitOfWork() as uow:
        remaining = await uow.idempotency.lease_seconds_remaining(existing.id)
        if remaining is not None:
            raise IdempotencyInProgressError(retry_after_seconds=remaining)
        taken = await uow.idempotency.take_over_expired(existing.id, _LEASE)
        if taken is not None:
            claimed = _claimed(operation, taken)
            await uow.commit()
            logger.warning(
                "idempotency %s: took over record %s after its lease lapsed",
                operation,
                existing.id,
            )
            return claimed

    # The takeover matched nothing: another retry took the record over
    # first (its lease is now live), or the record changed state between
    # the two statements above. A fresh transaction, so now() is current.
    async with UnitOfWork() as uow:
        remaining = await uow.idempotency.lease_seconds_remaining(existing.id)
    raise IdempotencyInProgressError(retry_after_seconds=remaining if remaining is not None else 1)
