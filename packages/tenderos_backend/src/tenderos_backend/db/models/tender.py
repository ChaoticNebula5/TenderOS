import uuid
from datetime import date, datetime

from sqlalchemy import TIMESTAMP, BigInteger, ForeignKey, Index, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Tender(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tenders"
    __table_args__ = (Index("idx_tenders_org_status", "organization_id", "status"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    client_name: Mapped[str | None] = mapped_column(nullable=True)
    submission_date: Mapped[date | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, default="created", server_default="created")
    state_version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
