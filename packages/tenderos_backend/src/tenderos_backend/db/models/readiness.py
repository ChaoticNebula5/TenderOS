import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, TIMESTAMP, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class ReadinessReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "readiness_reports"
    __table_args__ = (
        UniqueConstraint(
            "tender_id", "tender_state_version", name="uq_readiness_report_version"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Both JSONB, not two separate tables - readiness is a point-in-time
    # snapshot read as a whole (one GET /readiness call wants the full
    # picture), not queried by individual blocker/count the way
    # redteam_findings needs per-row resolve. counts: the PRD's fuller
    # §10.13 list (visible, non-blocking). blockers: the Build Guide's
    # narrower "required blockers" list (refusal-triggering) - see the B12
    # plan's Q5 for why these are deliberately two different lists sourced
    # from the two different docs, not one merged list.
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    blockers: Mapped[list] = mapped_column(JSONB, nullable=False)
    ready: Mapped[bool] = mapped_column(Boolean, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
