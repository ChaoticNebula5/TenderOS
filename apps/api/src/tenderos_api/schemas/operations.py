from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from tenderos_backend.db.models.async_operation import AsyncOperation


class OperationResponse(BaseModel):
    """An async operation's state as the web app sees it. The work's results
    are read from the feature's own routes (e.g. the red-team findings list)
    once status is "succeeded"; on "failed", error_code and error_message
    are safe to show (never exception text)."""

    operation_id: UUID
    tender_id: UUID
    kind: str
    status: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_operation(cls, operation: AsyncOperation) -> "OperationResponse":
        return cls(
            operation_id=operation.id,
            tender_id=operation.tender_id,
            kind=operation.kind,
            status=operation.status,
            error_code=operation.error_code,
            error_message=operation.error_message,
            created_at=operation.created_at,
            started_at=operation.started_at,
            finished_at=operation.finished_at,
        )
