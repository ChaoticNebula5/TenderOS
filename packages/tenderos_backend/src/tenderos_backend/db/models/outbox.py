import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, Index, func
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
