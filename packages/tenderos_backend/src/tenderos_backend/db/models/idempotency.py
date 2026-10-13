import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class IdempotencyRecord(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress','completed','failed')", name="ck_idempotency_status"
        ),
        UniqueConstraint(
            "organization_id", "operation", "idempotency_key", name="uq_idempotency_key"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(nullable=False)
    request_hash: Mapped[str] = mapped_column(nullable=False)
    response_status: Mapped[int | None] = mapped_column(nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Phase 4 blocker 2 (migration 019): the lease on an in_progress record.
    # locked_until is when the current holder's claim lapses (always set from
    # the database clock); claim_token identifies the holder, so a request
    # whose lease was taken over can no longer complete or release the
    # record. Both NULL on rows created before migration 019, which count as
    # expired: such a row can be taken over, never stranded.
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
