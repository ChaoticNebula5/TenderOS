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
        request_hash = hashlib.sha256(body_bytes).hexdigest()

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
