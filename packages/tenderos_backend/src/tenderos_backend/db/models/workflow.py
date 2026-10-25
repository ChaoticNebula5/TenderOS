import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Integer, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class WorkflowRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','waiting_for_human','completed','failed','cancelled')",
            name="ck_workflow_run_status",
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    langgraph_thread_id: Mapped[str] = mapped_column(nullable=False, unique=True)
    status: Mapped[str] = mapped_column(nullable=False)
    plan_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    prompt_bundle_version: Mapped[str] = mapped_column(nullable=False)
    model_config_json: Mapped[dict] = mapped_column("model_config", JSONB, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # B16 Group 4: the real, mutable retry countdown - see migration 016's
    # own docstring for why this exists instead of using TenderState.
    # retry_budget_remaining (that field is LangGraph-checkpointed state,
    # not a plain column, so nothing outside a running graph.ainvoke()
    # call can read or decrement it).
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class WorkflowTask(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','skipped','blocked')",
            name="ck_workflow_task_status",
        ),
        UniqueConstraint("workflow_run_id", "task_key", name="uq_workflow_task_key"),
    )

    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(nullable=False)
    task_type: Mapped[str] = mapped_column(nullable=False)
    assigned_agent: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending", server_default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    input_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    output_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    error_code: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class WorkflowTaskDependency(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "workflow_task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_tasks.id", ondelete="CASCADE"), nullable=False
    )


class ApprovalGate(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approval_gates"
    __table_args__ = (
        CheckConstraint(
            "gate_type IN ('requirement_review','compliance_review','resource_selection',"
            "'commercial_approval','finding_resolution','final_readiness','cost_budget')",
            name="ck_approval_gate_type",
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected','changes_requested')",
            name="ck_approval_gate_status",
        ),
        UniqueConstraint("workflow_run_id", "gate_key", name="uq_gate_key"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False
    )
    gate_key: Mapped[str] = mapped_column(nullable=False)
    gate_type: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, default="pending", server_default="pending")
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    decision_payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
