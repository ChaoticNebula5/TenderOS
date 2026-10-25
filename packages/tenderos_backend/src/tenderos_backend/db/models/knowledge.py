import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    REAL,
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class KnowledgeImportBatch(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_import_batches"
    __table_args__ = (
        CheckConstraint(
            "import_type IN ("
            "'bulk_resumes','employee_master_csv','employee_master_xlsx',"
            "'certification_documents','manual'"
            ")",
            name="ck_knowledge_import_batch_type",
        ),
        CheckConstraint(
            "status IN ('queued','processing','needs_review','completed','failed')",
            name="ck_knowledge_import_batch_status",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    import_type: Mapped[str] = mapped_column(nullable=False)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(nullable=False, default="queued", server_default="queued")
    total_items: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    completed_items: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    failed_items: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class KnowledgeImportItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_import_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','extracted','needs_review','approved','rejected','failed')",
            name="ck_knowledge_import_item_status",
        ),
        UniqueConstraint("batch_id", "source_ref", name="uq_knowledge_import_item_source_ref"),
    )

    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_ref: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending", server_default="pending")
    extracted_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    target_knowledge_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "knowledge_items.id", ondelete="SET NULL", name="fk_import_item_target"
        ),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        CheckConstraint(
            "item_type IN ("
            "'employee','certification','past_project','capability',"
            "'company_document','client_reference','partner'"
            ")",
            name="ck_knowledge_item_type",
        ),
        CheckConstraint(
            "status IN ('active','expired','archived','unverified')",
            name="ck_knowledge_item_status",
        ),
        UniqueConstraint(
            "organization_id",
            "item_type",
            "content_fingerprint",
            name="uq_knowledge_item_fingerprint",
        ),
        # 005_knowledge.py
        Index("idx_knowledge_items_org_type", "organization_id", "item_type", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    item_type: Mapped[str] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="active", server_default="active")
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    # DB column is "metadata" per Part C's DDL - Base.metadata is reserved by
    # SQLAlchemy's declarative machinery, so the Python attribute is
    # metadata_ while the underlying column name stays "metadata".
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeItemChunk(Base):
    """Many-to-many association between a knowledge_item and every
    document_chunk it's evidenced by - unlike knowledge_items.source_chunk_id
    (a scalar FK, correct for certifications/past_projects/capabilities,
    which are each genuinely sourced from exactly one chunk), an employee's
    résumé chunks into several sections (summary, experience, certifications,
    education), so a single FK column can't represent "this employee is
    evidenced by N chunks" no matter which one it points at. Composite
    primary key, no surrogate id or created_at - a pure association, nothing
    else in this schema references one of these rows individually the way
    knowledge_relationships (a richer entity with its own confidence/verified
    fields) is referenced.
    """

    __tablename__ = "knowledge_item_chunks"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="CASCADE"),
        primary_key=True,
    )


class Employee(Base):
    __tablename__ = "employees"
    __table_args__ = (
        CheckConstraint(
            "availability_status IN "
            "('available','partially_available','unavailable','unknown')",
            name="ck_employee_availability_status",
        ),
        CheckConstraint(
            "profile_review_status IN "
            "('needs_review','verified','stale','rejected','archived')",
            name="ck_employee_profile_review_status",
        ),
        # Partial unique index, not UniqueConstraint - same reason as
        # documents.py's uq_tender_document_hash/uq_org_document_hash
        # (Postgres unique constraints can't carry a WHERE clause).
        # Migration 005_knowledge.py created this directly via
        # op.create_index; never mirrored here until B16's own partial-
        # index audit found it - the same gap, second instance, found by
        # auditing every op.create_index/op.create_check_constraint/
        # op.create_unique_constraint in every migration against what the
        # models declare, not by another live failure.
        Index(
            "uq_employee_code_per_org",
            "organization_id",
            "employee_code",
            unique=True,
            postgresql_where="employee_code IS NOT NULL",
        ),
        # 005_knowledge.py
        Index(
            "idx_employees_bid_pool",
            "organization_id",
            "eligible_for_bid",
            "profile_review_status",
            "availability_status",
        ),
    )

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    employee_code: Mapped[str | None] = mapped_column(nullable=True)
    name: Mapped[str] = mapped_column(nullable=False)
    email: Mapped[str | None] = mapped_column(nullable=True)
    current_title: Mapped[str | None] = mapped_column(nullable=True)
    years_experience: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    professional_location: Mapped[str | None] = mapped_column(nullable=True)
    availability_status: Mapped[str] = mapped_column(
        nullable=False, default="unknown", server_default="unknown"
    )
    availability_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    eligible_for_bid: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    profile_review_status: Mapped[str] = mapped_column(
        nullable=False, default="needs_review", server_default="needs_review"
    )
    profile_last_verified_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    profile_verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    resume_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    parsed_profile: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class Certification(Base):
    __tablename__ = "certifications"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    holder_employee_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True
    )
    issuer: Mapped[str | None] = mapped_column(nullable=True)
    credential_id: Mapped[str | None] = mapped_column(nullable=True)


class PastProject(Base):
    __tablename__ = "past_projects"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    client_name: Mapped[str | None] = mapped_column(nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)


class Capability(Base):
    __tablename__ = "capabilities"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category: Mapped[str | None] = mapped_column(nullable=True)
    description: Mapped[str | None] = mapped_column(nullable=True)


class CompanyDocument(Base):
    __tablename__ = "company_documents"

    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    doc_subtype: Mapped[str] = mapped_column(nullable=False)


class KnowledgeRelationship(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "knowledge_relationships"
    __table_args__ = (
        CheckConstraint(
            "relationship_type IN ('WORKED_ON','DEMONSTRATES')",
            name="ck_knowledge_relationship_type",
        ),
        UniqueConstraint(
            "organization_id", "content_fingerprint", name="uq_knowledge_relationship_fingerprint"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    source_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(nullable=False)
    target_knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    content_fingerprint: Mapped[str] = mapped_column(nullable=False)


class TenderKnowledgeSelection(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "tender_knowledge_selections"
    __table_args__ = (
        UniqueConstraint(
            "tender_id", "knowledge_item_id", name="uq_tender_knowledge_selection"
        ),
        # 005_knowledge.py
        Index("idx_tender_knowledge_selected", "tender_id", "selected"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_items.id", ondelete="CASCADE"), nullable=False
    )
    selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    selected_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    selected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    deselected_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    deselected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    change_reason: Mapped[str | None] = mapped_column(nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
