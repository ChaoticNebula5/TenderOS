import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class GraphProjectionRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "graph_projection_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','completed','failed')",
            name="ck_graph_projection_run_status",
        ),
        # 010_graph.py: newest projection first per tender.
        Index("idx_graph_projection_latest", "tender_id", text("source_state_version DESC")),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    source_state_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(nullable=False)
    node_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    relationship_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
