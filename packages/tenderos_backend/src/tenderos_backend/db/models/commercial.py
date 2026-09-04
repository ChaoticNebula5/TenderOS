import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class PricingDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pricing_documents"
    __table_args__ = (
        UniqueConstraint("document_id", "sheet_name", name="uq_pricing_document_sheet"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    sheet_name: Mapped[str] = mapped_column(nullable=False)
    currency: Mapped[str | None] = mapped_column(nullable=True)


class PricingLineItem(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pricing_line_items"
    __table_args__ = (
        UniqueConstraint("pricing_document_id", "cell_ref", name="uq_pricing_line_item_cell"),
        UniqueConstraint(
            "pricing_document_id", "content_fingerprint", name="uq_pricing_line_item_fingerprint"
        ),
    )

    pricing_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pricing_documents.id", ondelete="CASCADE"), nullable=False
    )
    cell_ref: Mapped[str] = mapped_column(nullable=False)
    description: Mapped[str | None] = mapped_column(nullable=True)
    raw_value: Mapped[str | None] = mapped_column(nullable=True)
    numeric_value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    formula: Mapped[str | None] = mapped_column(nullable=True)
    currency: Mapped[str | None] = mapped_column(nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)


class PricingFinding(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "pricing_findings"
    __table_args__ = (
        CheckConstraint(
            "finding_type IN ("
            "'missing_cell','formula_error','wrong_total','currency_mismatch',"
            "'duplicate_cost','cross_doc_mismatch','unpriced_requirement',"
            "'needs_human_review'"
            ")",
            name="ck_pricing_finding_type",
        ),
        CheckConstraint(
            "severity IN ('critical','major','minor')", name="ck_pricing_finding_severity"
        ),
        UniqueConstraint("tender_id", "content_fingerprint", name="uq_pricing_finding_fingerprint"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    finding_type: Mapped[str] = mapped_column(nullable=False)
    # B16 Group 6: which uploaded document this finding came from. NULL
    # for cross_doc_mismatch by design (that finding type's two documents
    # are already both named, symmetrically, via item_a_ref/item_b_ref
    # below); set at creation time for every other finding type. See
    # migration 015's own docstring for why this is a real provenance
    # gap being closed, not only grounding enablement, and why it can't
    # be backfilled for pre-existing rows.
    pricing_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pricing_documents.id", ondelete="SET NULL"), nullable=True
    )
    item_a_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    item_b_ref: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    delta: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    severity: Mapped[str] = mapped_column(nullable=False, default="major")
    note: Mapped[str] = mapped_column(nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
