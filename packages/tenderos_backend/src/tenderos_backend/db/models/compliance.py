import uuid
from datetime import datetime

from sqlalchemy import REAL, TIMESTAMP, Boolean, CheckConstraint, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class EvidenceLink(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        CheckConstraint(
            "source_kind IN ("
            "'employee','certification','past_project','capability',"
            "'company_document','client_reference','partner','document_excerpt'"
            ")",
            name="ck_evidence_link_source_kind",
        ),
        CheckConstraint(
            "validity_status IN ('valid','invalid','expired','unknown')",
            name="ck_evidence_link_validity_status",
        ),
        CheckConstraint(
            "knowledge_item_id IS NOT NULL OR source_chunk_id IS NOT NULL",
            name="ck_evidence_link_has_source",
        ),
        UniqueConstraint("tender_id", "content_fingerprint", name="uq_evidence_link_fingerprint"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    source_kind: Mapped[str] = mapped_column(nullable=False)
    excerpt: Mapped[str | None] = mapped_column(nullable=True)
    confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    validity_status: Mapped[str] = mapped_column(nullable=False, default="unknown")
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)


class ComplianceEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "compliance_entries"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'fully_compliant','partially_compliant','non_compliant',"
            "'clarification_required','evidence_unavailable','not_reviewed'"
            ")",
            name="ck_compliance_entry_status",
        ),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(nullable=False)
    reasoning_summary: Mapped[str | None] = mapped_column(nullable=True)
    confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    human_approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ComplianceEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "compliance_evidence"
    __table_args__ = (
        UniqueConstraint(
            "compliance_entry_id", "evidence_link_id", name="uq_compliance_evidence"
        ),
    )

    compliance_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("compliance_entries.id", ondelete="CASCADE"), nullable=False
    )
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_links.id", ondelete="CASCADE"), nullable=False
    )
