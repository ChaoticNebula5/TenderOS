import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    UniqueConstraint,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class ProposalSection(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposal_sections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','review','approved')", name="ck_proposal_section_status"
        ),
        UniqueConstraint(
            "tender_id", "section_name", "version", name="uq_proposal_section_version"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    section_name: Mapped[str] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(nullable=False, default="", server_default=text("''"))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(
        nullable=False, default="draft", server_default=text("'draft'")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ProposalClaim(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposal_claims"
    __table_args__ = (
        CheckConstraint(
            "claim_type IN ('capability','experience','team','compliance','commercial','other')",
            name="ck_proposal_claim_type",
        ),
        CheckConstraint(
            "grounding_status IN ('grounded','unsupported','needs_review','not_required')",
            name="ck_proposal_claim_grounding_status",
        ),
        CheckConstraint(
            "claim_kind IN ('current','commitment')",
            name="ck_proposal_claim_kind",
        ),
        UniqueConstraint("section_id", "content_fingerprint", name="uq_proposal_claim_fingerprint"),
    )

    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal_sections.id", ondelete="CASCADE"), nullable=False
    )
    claim_text: Mapped[str] = mapped_column(nullable=False)
    claim_type: Mapped[str] = mapped_column(nullable=False)
    is_factual: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    # Orthogonal to is_factual, not a replacement - is_factual asks "is this
    # specific and checkable at all"; claim_kind asks "checkable against
    # what we already have (current) or what we're promising for this
    # contract (commitment)". A commitment claim still goes through
    # enforce_claim_grounding exactly like any other is_factual=true claim
    # (unchanged by this addition - see that function's own comment) and
    # still surfaces visibly as "unsupported" when it has no evidence,
    # never silently exempted the way is_factual=false is. This field only
    # changes which bucket a citation-correctness measurement counts a
    # claim into - see KNOWN_LIMITATIONS.md's [B9/B16] entry.
    claim_kind: Mapped[str] = mapped_column(
        nullable=False, default="current", server_default="current"
    )
    grounding_status: Mapped[str] = mapped_column(
        nullable=False, default="needs_review", server_default=text("'needs_review'")
    )
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class ProposalClaimEvidence(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposal_claim_evidence"
    __table_args__ = (
        UniqueConstraint(
            "proposal_claim_id", "evidence_link_id", name="uq_proposal_claim_evidence"
        ),
    )

    proposal_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal_claims.id", ondelete="CASCADE"), nullable=False
    )
    evidence_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_links.id", ondelete="CASCADE"), nullable=False
    )


class ProposalClaimRequirement(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposal_claim_requirements"
    __table_args__ = (
        UniqueConstraint(
            "proposal_claim_id", "requirement_id", name="uq_proposal_claim_requirement"
        ),
    )

    proposal_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal_claims.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )


class ProposalClaimEmployee(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "proposal_claim_employees"
    __table_args__ = (
        UniqueConstraint(
            "proposal_claim_id",
            "employee_knowledge_item_id",
            name="uq_proposal_claim_employee",
        ),
    )

    proposal_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposal_claims.id", ondelete="CASCADE"), nullable=False
    )
    employee_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
