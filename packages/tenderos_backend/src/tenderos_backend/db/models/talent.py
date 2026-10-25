import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class RoleRequirement(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role_requirements"
    __table_args__ = (
        UniqueConstraint(
            "tender_id", "content_fingerprint", name="uq_role_requirement_fingerprint"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    role_name: Mapped[str] = mapped_column(nullable=False)
    min_experience_years: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    required_skills: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    required_certs: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id"), nullable=True
    )
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)


class RoleRequirementSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role_requirement_sources"
    __table_args__ = (
        UniqueConstraint(
            "role_requirement_id", "requirement_id", name="uq_role_requirement_source"
        ),
    )

    role_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role_requirements.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )


class RoleMatch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role_matches"
    __table_args__ = (
        UniqueConstraint(
            "role_requirement_id", "employee_knowledge_item_id", name="uq_role_match_candidate"
        ),
    )

    role_requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role_requirements.id", ondelete="CASCADE"), nullable=False
    )
    employee_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float | None] = mapped_column(REAL, nullable=True)
    strengths: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    gaps: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    human_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class RoleMatchEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role_match_evidence"
    __table_args__ = (
        UniqueConstraint(
            "role_match_id", "evidence_link_id", "criterion", name="uq_role_match_evidence"
        ),
    )

    role_match_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("role_matches.id", ondelete="CASCADE"), nullable=False
    )
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_links.id", ondelete="CASCADE"), nullable=False
    )
    criterion: Mapped[str] = mapped_column(nullable=False)
