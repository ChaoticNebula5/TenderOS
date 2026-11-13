import uuid
from datetime import datetime

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

# Phase 4 plan item 43: the statuses in which a run is still in progress. A
# run waiting at a gate is active, and so is one with a retry scheduled (it
# stays "running" until the retry succeeds or the budget runs out). The
# others are terminal: nothing moves a run out of them on purpose.
# The web screen's NON_TERMINAL_STATUSES is the same set.
ACTIVE_RUN_STATUSES = ("queued", "running", "waiting_for_human")
# Plan item 44 (the cancellation race): nothing moves a run out of these.
# WorkflowRepository.update_run_status refuses to, in its own UPDATE.
# "changes_requested" (plan item 71, migration 029, decision 0093): a
# reviewer's Request changes ends the run at its gate. Not one of Part C's
# six values; added on purpose, as migration 018 added the cost_budget gate.
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled", "changes_requested")
# Every value ck_workflow_run_status allows (Part C's six plus item 71's).
RUN_STATUSES = ACTIVE_RUN_STATUSES + TERMINAL_RUN_STATUSES

# Plan item 19: why a failed run failed, shown under "Last run". The run
# reaper writes the first two (domain/services/run_reaper.py), the RQ
# timeout path the third. A run failed any other way (a graph failure with
# no retry left, a human's reject) has none.
RUN_WORKER_LOST = "WORKER_LOST"
RUN_DISPATCH_FAILED = "DISPATCH_FAILED"
RUN_JOB_TIMEOUT = "JOB_TIMEOUT"
RUN_ERROR_CODES = (RUN_WORKER_LOST, RUN_DISPATCH_FAILED, RUN_JOB_TIMEOUT)


class WorkflowRun(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ",".join(f"'{s}'" for s in RUN_STATUSES) + ")",
            name="ck_workflow_run_status",
        ),
        # At most one active run per tender (plan item 43, migration 022).
        # A second insert, or a status write that would make a second run
        # active, fails with a unique violation instead of leaving two runs
        # working on one tender.
        Index(
            "uq_workflow_runs_one_active_per_tender",
            "tender_id",
            unique=True,
            postgresql_where=text(
                "status IN (" + ", ".join(f"'{s}'" for s in ACTIVE_RUN_STATUSES) + ")"
            ),
        ),
        CheckConstraint(
            "error_code IN (" + ",".join(f"'{c}'" for c in RUN_ERROR_CODES) + ")",
            name="ck_workflow_run_error_code",
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
    # Plan item 19 (migration 024). The RQ job now responsible for moving
    # this run forward, and when that was recorded (database clock): the
    # start and resume claims write them in the same UPDATE that moves the
    # run to running, and so does the scheduling of a retry. The run
    # reaper (run_reaper.py) asks RQ about this job, never about the last
    # job to have run: a run waiting for its retry always has a failed job
    # behind it, kept on purpose (I4).
    current_job_id: Mapped[str | None] = mapped_column(nullable=True)
    current_job_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(nullable=True)


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
