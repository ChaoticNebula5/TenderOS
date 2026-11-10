"""organization knowledge: knowledge_items, employees, certifications,
past_projects, capabilities, company_documents, knowledge_relationships,
tender_knowledge_selections, knowledge_import_batches, knowledge_import_items

Revision ID: 005_knowledge
Revises: 004_requirements
Create Date: 2026-08-11 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "005_knowledge"
down_revision: str | None = "004_requirements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # agent_traces was created in 004_requirements with tender_id NOT NULL -
    # correct at the time, since every agent call B5 made was tender-scoped.
    # B6 introduces the first organization-scoped agent calls (resume/
    # certification extraction, WORKED_ON/DEMONSTRATES inference run against
    # org-level knowledge, not any one tender), so E1's "every LLM-backed
    # agent records model/tokens/latency/cost" requirement now needs a row
    # shape that fits both. Mirrors the tender_id-XOR-organization_id split
    # documents already uses (ck_document_scope) rather than inventing a new
    # pattern. No backfill needed - no org-scoped rows exist yet.
    op.alter_column("agent_traces", "tender_id", nullable=True)
    op.add_column(
        "agent_traces",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_agent_trace_scope",
        "agent_traces",
        "(tender_id IS NOT NULL AND organization_id IS NULL) "
        "OR (tender_id IS NULL AND organization_id IS NOT NULL)",
    )

    # Part C orders knowledge_import_batches/items before knowledge_items,
    # with target_knowledge_item_id's FK deferred to an ALTER after
    # knowledge_items exists - same deferred-FK pattern B3/B4 already used
    # for forward references, followed here rather than reordered.
    op.create_table(
        "knowledge_import_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("import_type", sa.Text(), nullable=False),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("total_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_items", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "import_type IN ("
            "'bulk_resumes','employee_master_csv','employee_master_xlsx',"
            "'certification_documents','manual'"
            ")",
            name="ck_knowledge_import_batch_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued','processing','needs_review','completed','failed')",
            name="ck_knowledge_import_batch_status",
        ),
    )

    op.create_table(
        "knowledge_import_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_import_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("extracted_payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        # FK to knowledge_items added below, after that table exists.
        sa.Column("target_knowledge_item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('pending','extracted','needs_review','approved','rejected','failed')",
            name="ck_knowledge_import_item_status",
        ),
        sa.UniqueConstraint("batch_id", "source_ref", name="uq_knowledge_import_item_source_ref"),
    )

    op.create_table(
        "knowledge_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "item_type IN ("
            "'employee','certification','past_project','capability',"
            "'company_document','client_reference','partner'"
            ")",
            name="ck_knowledge_item_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','expired','archived','unverified')",
            name="ck_knowledge_item_status",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "item_type",
            "content_fingerprint",
            name="uq_knowledge_item_fingerprint",
        ),
    )
    op.create_index(
        "idx_knowledge_items_org_type",
        "knowledge_items",
        ["organization_id", "item_type", "status"],
    )

    op.create_foreign_key(
        "fk_import_item_target",
        "knowledge_import_items",
        "knowledge_items",
        ["target_knowledge_item_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "employees",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("employee_code", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("current_title", sa.Text(), nullable=True),
        sa.Column("years_experience", sa.Numeric(), nullable=True),
        sa.Column("professional_location", sa.Text(), nullable=True),
        sa.Column("availability_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("availability_as_of", sa.Date(), nullable=True),
        sa.Column("eligible_for_bid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "profile_review_status", sa.Text(), nullable=False, server_default="needs_review"
        ),
        sa.Column("profile_last_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "profile_verified_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column(
            "resume_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("parsed_profile", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.CheckConstraint(
            "availability_status IN ('available','partially_available','unavailable','unknown')",
            name="ck_employee_availability_status",
        ),
        sa.CheckConstraint(
            "profile_review_status IN ('needs_review','verified','stale','rejected','archived')",
            name="ck_employee_profile_review_status",
        ),
    )
    op.create_index(
        "uq_employee_code_per_org",
        "employees",
        ["organization_id", "employee_code"],
        unique=True,
        postgresql_where=sa.text("employee_code IS NOT NULL"),
    )
    op.create_index(
        "idx_employees_bid_pool",
        "employees",
        ["organization_id", "eligible_for_bid", "profile_review_status", "availability_status"],
    )

    op.create_table(
        "certifications",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "holder_employee_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("issuer", sa.Text(), nullable=True),
        sa.Column("credential_id", sa.Text(), nullable=True),
    )

    op.create_table(
        "past_projects",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("client_name", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )

    op.create_table(
        "capabilities",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
    )

    op.create_table(
        "company_documents",
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("doc_subtype", sa.Text(), nullable=False),
    )

    op.create_table(
        "knowledge_relationships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column(
            "target_knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "relationship_type IN ('WORKED_ON','DEMONSTRATES')",
            name="ck_knowledge_relationship_type",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "content_fingerprint",
            name="uq_knowledge_relationship_fingerprint",
        ),
    )

    op.create_table(
        "tender_knowledge_selections",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("selected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "selected_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("deselected_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "deselected_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("tender_id", "knowledge_item_id", name="uq_tender_knowledge_selection"),
    )
    op.create_index(
        "idx_tender_knowledge_selected",
        "tender_knowledge_selections",
        ["tender_id", "selected"],
    )


def downgrade() -> None:
    op.drop_index("idx_tender_knowledge_selected", table_name="tender_knowledge_selections")
    op.drop_table("tender_knowledge_selections")
    op.drop_table("knowledge_relationships")
    op.drop_table("company_documents")
    op.drop_table("capabilities")
    op.drop_table("past_projects")
    op.drop_table("certifications")
    op.drop_index("idx_employees_bid_pool", table_name="employees")
    op.drop_index("uq_employee_code_per_org", table_name="employees")
    op.drop_table("employees")
    op.drop_constraint("fk_import_item_target", "knowledge_import_items", type_="foreignkey")
    op.drop_index("idx_knowledge_items_org_type", table_name="knowledge_items")
    op.drop_table("knowledge_items")
    op.drop_table("knowledge_import_items")
    op.drop_table("knowledge_import_batches")
    op.drop_constraint("ck_agent_trace_scope", "agent_traces", type_="check")
    op.drop_column("agent_traces", "organization_id")
    op.alter_column("agent_traces", "tender_id", nullable=False)
