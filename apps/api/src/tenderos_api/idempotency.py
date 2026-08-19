import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta  # noqa: F401 - UTC/datetime used by TODO below
from uuid import UUID

from fastapi import Request
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
            f"{request.method}\n{request.url.path}\n{request.url.query}\n".encode("utf-8")
        )
        request_hash = hashlib.sha256(request_identity + body_bytes).hexdigest()

        async with UnitOfWork() as uow:
            existing = await uow.idempotency.get(organization_id, operation, idempotency_key)

            if existing is None:
                record = await uow.idempotency.create_in_progress(
                    organization_id=organization_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    expires_at=datetime.now(UTC) + _RECORD_TTL,
                )
                await uow.commit()

                return IdempotencyContext(
                    active=True,
                    record_id=record.id,
                )

            if existing.request_hash != request_hash:
                raise IdempotencyConflictError()

            if existing.status == "completed":
                if existing.response_status is None or existing.response_body is None:
                    raise RuntimeError("Completed idempotency record has no saved response")

                return IdempotencyContext(
                    active=True, replay_response=(existing.response_status, existing.response_body)
                )

            else:
                return IdempotencyContext(active=True, record_id=existing.id)

    return _dependency
