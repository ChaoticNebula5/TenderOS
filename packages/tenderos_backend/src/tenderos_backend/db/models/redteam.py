import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class RedteamFinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "redteam_findings"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical','major','minor')",
            name="ck_redteam_finding_severity",
        ),
        CheckConstraint(
            "resolution_source IN ('system','human')",
            name="ck_redteam_finding_resolution_source",
        ),
        UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_redteam_finding_fingerprint"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    finding_category: Mapped[str] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(nullable=False)
    finding_text: Mapped[str] = mapped_column(nullable=False)
    rebuttal: Mapped[str | None] = mapped_column(nullable=True)
    evidence_refs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    violated_condition: Mapped[str] = mapped_column(nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    resolution_note: Mapped[str | None] = mapped_column(nullable=True)
    # NULL until resolved; 'human' (resolve_finding) or 'system'
    # (auto_retire_undetected_findings) once it is. Lets _persist_draft
    # reopen a system-retired finding that reappears while leaving a
    # human's resolution untouched, without string-matching resolution_note.
    resolution_source: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
