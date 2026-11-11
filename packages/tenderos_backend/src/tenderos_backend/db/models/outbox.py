import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class OutboxEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('pending','dispatched','failed')", name="ck_outbox_status"),
        Index(
            "idx_outbox_pending",
            "status",
            "available_at",
            postgresql_where="status IN ('pending','failed')",
        ),
        # Plan item 55 (migration 026, decision 0082): the SSE stream's filter,
        # whose keys tenderos_api/outbox_stream.py writes as literals so a
        # generic plan can use these; and retention's oldest-dispatched
        # lookup (OutboxRepository.delete_old_dispatched), which the stream
        # never uses. The migration builds them CONCURRENTLY.
        Index("idx_outbox_workflow_run_id", text("(payload ->> 'workflow_run_id'::text)")),
        Index("idx_outbox_operation_id", text("(payload ->> 'operation_id'::text)")),
        Index(
            "idx_outbox_dispatched_at",
            "dispatched_at",
            postgresql_where="status = 'dispatched'",
        ),
    )

    aggregate_type: Mapped[str] = mapped_column(nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending", server_default="pending")
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(nullable=False, unique=True)
