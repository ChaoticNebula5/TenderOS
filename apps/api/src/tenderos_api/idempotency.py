import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta  # noqa: F401 - UTC/datetime used by TODO below
from uuid import UUID

from fastapi import Request
from sqlalchemy.exc import IntegrityError
from tenderos_backend.db.models.idempotency import IdempotencyRecord
from tenderos_backend.db.unit_of_work import UnitOfWork

from tenderos_api.dependencies import _resolve_organization_id
from tenderos_api.errors import IdempotencyConflictError, IdempotencyKeyRequiredError

_RECORD_TTL = timedelta(hours=24)


@dataclass
class IdempotencyContext:
    active: bool
    record_id: UUID | None = None
    replay_response: tuple[int, dict] | None = None

    async def finalize(self, status_code: int, body: dict) -> None:
        if not self.active or self.record_id is None:
            return
        async with UnitOfWork() as uow:
            await uow.idempotency.complete(self.record_id, status_code, body)
            await uow.commit()


def get_idempotency_context(
    operation: str, required: bool = False
) -> Callable[..., Awaitable[IdempotencyContext]]:
    """Idempotency-Key was optional-but-honored for every B2 operation: Part I1
    only requires it for create workflow run / gate decision / resource
    selection / knowledge selection change / proposal generation / graph
    projection trigger, none of which existed in B2. B3 introduces the first
    two of those (create_workflow_run, gate_decision) - `required=True` on
    those call sites makes the header actually mandatory (400
    IDEMPOTENCY_KEY_REQUIRED if absent) rather than silently falling back to
    active=False, matching I1's literal text instead of B2's blanket
    optional-everywhere default.
    """

    async def _dependency(request: Request) -> IdempotencyContext:
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key is None:
            if required:
                raise IdempotencyKeyRequiredError()
            return IdempotencyContext(active=False)

        organization_id = await _resolve_organization_id(request)
        body_bytes = await request.body()
        # The lookup key below is (organization_id, operation, idempotency_
        # key) - operation is always a fixed literal per route (e.g.
        # "graph_projection_trigger"), never the resource id in the path.
        # Two different resources of the same type in the same org (two
        # tenders, two gates) hit with the same Idempotency-Key would
        # otherwise collide on one record and the second request would
        # silently replay the first's response instead of running its own
        # mutation - demonstrated live by
        # test_duplicate_idempotency_key_replays_wrong_tenders_graph_
        # projection. request.method + request.url.path (the resolved path,
        # real ids substituted in - not the route template) folds the
        # resource identity into the hash, so a same-key request against a
        # different resource now hits the existing request_hash mismatch
        # branch below and 409s as IDEMPOTENCY_CONFLICT, the same real
        # conflict a different body already produces - not new
        # error-handling, just a wider definition of "the same request."
        # url.query is folded in for the same reason path is - a route that
        # reads query params (e.g. a filter/pagination arg) could otherwise
        # have two materially different requests share one record just
        # because their path and body happened to match.
        request_identity = (
            f"{request.method}\n{request.url.path}\n{request.url.query}\n".encode()
        )
        request_hash = hashlib.sha256(request_identity + body_bytes).hexdigest()

        async with UnitOfWork() as uow:
            existing = await uow.idempotency.get(organization_id, operation, idempotency_key)

            if existing is None:
                # B16: select-then-insert - two genuinely concurrent requests
                # with the same key can both observe "no existing record"
                # here and both attempt to create one, the loser hitting
                # uq_idempotency_key. NOT the same shape as document_
                # service.create_document's own fix despite looking
                # identical at first glance - checked, not assumed:
                # create_document's reread finds the WINNER'S FINISHED row
                # (a real Document, immediately usable), because a second,
                # independent constraint (uq_tender_document_hash) is what
                # both concurrent inserts actually race on. Here, the
                # reread finds only a marker that a sibling is CURRENTLY
                # doing the real work - the created tender doesn't exist
                # yet.
                try:
                    record = await uow.idempotency.create_in_progress(
                        organization_id=organization_id,
                        operation=operation,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        expires_at=datetime.now(UTC) + _RECORD_TTL,
                    )
                    await uow.commit()
                except IntegrityError:
                    await uow.rollback()
                    async with UnitOfWork() as reread_uow:
                        existing = await reread_uow.idempotency.get(
                            organization_id, operation, idempotency_key
                        )
                    if existing is None:
                        raise
                    return _context_for_existing_record(existing, request_hash)

                return IdempotencyContext(
                    active=True,
                    record_id=record.id,
                )

            return _context_for_existing_record(existing, request_hash)

    return _dependency


def _context_for_existing_record(
    existing: IdempotencyRecord, request_hash: str
) -> IdempotencyContext:
    """Shared by both call sites in _dependency above (the ordinary
    "a record already existed" read, and the reread after a caught
    IntegrityError) - one behavior, not two copies that could drift.

    B16, found by this file's own concurrent test, not assumed correct
    from the shape alone: an earlier version of this fix only changed the
    IntegrityError branch, reasoning that a fresh constraint violation
    means a sibling is live RIGHT NOW while an ordinary "existing, still
    in_progress" read might legitimately be an hours-old crashed request's
    leftover that a retry should be allowed to take over. That reasoning
    was real but incomplete: create_in_progress's own commit (inside a
    short-lived UnitOfWork) finishes well before the WINNING request's own
    route handler finishes its real work and calls finalize() - so a
    second, genuinely concurrent request very often reaches THIS ordinary
    branch, not the IntegrityError branch, having simply run its own
    SELECT a moment after the winner's INSERT already committed. Treating
    that path as "safe to proceed" reproduced the exact bug this fix
    exists to close: two real, different tenders for one Idempotency-Key,
    confirmed live (asyncio.gather against the real ASGI app), not a
    500 - a silent duplicate, arguably worse.

    Consequence, named rather than silently accepted: this makes "not
    completed" always conflict, closing the concurrency bug but also
    closing the crash-recovery path this branch used to (nominally, if
    imperfectly) allow - a request that died before ever calling
    finalize() now leaves a permanently in_progress record that 409s
    every future retry with the same key, forever, since nothing
    currently reads expires_at to decide a record is abandoned rather
    than live (KNOWN_LIMITATIONS.md already names this - "expires_at is
    stored but never consulted by any read path"). That gap was already
    real before this fix (the old "let it proceed" behavior would have
    silently created a SECOND tender on a post-crash retry too, the
    identical bug via a different trigger) - not introduced here, but
    now surfaced loudly (409) instead of silently (a duplicate). Actually
    consulting expires_at to distinguish a live sibling from an abandoned
    one is real, separate design work, not a mechanical extension of
    this fix.
    """
    if existing.request_hash != request_hash:
        raise IdempotencyConflictError()

    if existing.status == "completed":
        if existing.response_status is None or existing.response_body is None:
            raise RuntimeError("Completed idempotency record has no saved response")

        return IdempotencyContext(
            active=True, replay_response=(existing.response_status, existing.response_body)
        )

    raise IdempotencyConflictError()
