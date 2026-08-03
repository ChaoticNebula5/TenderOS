import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta  # noqa: F401 - UTC/datetime used by TODO below
from uuid import UUID

from fastapi import Request
from tenderos_backend.db.unit_of_work import UnitOfWork

from tenderos_api.errors import IdempotencyConflictError

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


def get_idempotency_context(operation: str) -> Callable[..., Awaitable[IdempotencyContext]]:
    """Idempotency-Key is optional here: Part I1 only requires it for create
    workflow run / gate decision / resource selection / knowledge selection
    change / proposal generation / graph projection trigger - none of which
    exist yet in B2. Tender creation isn't on that list, so the header stays
    optional-but-honored: absent = normal request, present = full I1/I2 semantics.
    """

    async def _dependency(request: Request) -> IdempotencyContext:
        idempotency_key = request.headers.get("Idempotency-Key")
        if idempotency_key is None:
            return IdempotencyContext(active=False)

        organization_id = UUID(request.path_params["org_id"])
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
