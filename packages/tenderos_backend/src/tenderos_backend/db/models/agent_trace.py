import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, Numeric
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AgentTrace(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "agent_traces"
    __table_args__ = (
        # B6: tender_id was NOT NULL until organization-scoped agent calls
        # (resume/certification extraction, WORKED_ON/DEMONSTRATES) existed.
        # Exactly one of tender_id/organization_id is set per row, mirroring
        # documents.ck_document_scope.
        CheckConstraint(
            "(tender_id IS NOT NULL AND organization_id IS NULL) "
            "OR (tender_id IS NULL AND organization_id IS NOT NULL)",
            name="ck_agent_trace_scope",
        ),
    )

    tender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="SET NULL"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str | None] = mapped_column(nullable=True)
    model: Mapped[str | None] = mapped_column(nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(nullable=True)
    input_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    output_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    tool_names: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(nullable=True)
