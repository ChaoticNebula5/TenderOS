import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class Requirement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "requirements"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'technical','staffing','experience','commercial',"
            "'eligibility','submission','legal','security','other'"
            ")",
            name="ck_requirement_category",
        ),
        UniqueConstraint("tender_id", "content_fingerprint", name="uq_requirement_fingerprint"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    display_code: Mapped[str] = mapped_column(nullable=False)
    requirement_text: Mapped[str] = mapped_column(nullable=False)
    normalized_text: Mapped[str] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(nullable=False)
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id"), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    human_reviewed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    rejected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class SubmissionItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "submission_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('missing','present','needs_review','not_applicable')",
            name="ck_submission_item_status",
        ),
        UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_submission_item_fingerprint"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    mandatory: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id"), nullable=True
    )
    matched_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(nullable=False, default="missing", server_default="missing")
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)


class ClarificationItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "clarification_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open','answered','waived','closed')",
            name="ck_clarification_item_status",
        ),
        UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_clarification_item_fingerprint"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=True
    )
    question: Mapped[str] = mapped_column(nullable=False)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, default="open", server_default="open")
    resolution: Mapped[str | None] = mapped_column(nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
