import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AuditEvent(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('user','agent','system')", name="ck_audit_actor_type"),
        Index("idx_audit_tender_time", "tender_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    # FK added by migration 002_workflow.py once workflow_runs exists - 001_initial
    # created this column bare (workflow_runs didn't exist yet in B2).
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "workflow_runs.id", ondelete="SET NULL", name="fk_audit_events_workflow_run_id"
        ),
        nullable=True,
    )
    actor_type: Mapped[str] = mapped_column(nullable=False)
    actor_id: Mapped[str] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(nullable=False)
    target_type: Mapped[str | None] = mapped_column(nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    event_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
