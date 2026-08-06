"""workflow runs, tasks, task dependencies, approval gates

Revision ID: 002_workflow
Revises: 001_initial
Create Date: 2026-08-03 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_workflow"
down_revision: str | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
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
        sa.Column("langgraph_thread_id", sa.Text(), nullable=False, unique=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_bundle_version", sa.Text(), nullable=False),
        sa.Column("model_config", postgresql.JSONB(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','waiting_for_human','completed','failed','cancelled')",
            name="ck_workflow_run_status",
        ),
    )

    op.create_table(
        "workflow_tasks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_key", sa.Text(), nullable=False),
        sa.Column("task_type", sa.Text(), nullable=False),
        sa.Column("assigned_agent", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "input_refs", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "output_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','skipped','blocked')",
            name="ck_workflow_task_status",
        ),
        sa.UniqueConstraint("workflow_run_id", "task_key", name="uq_workflow_task_key"),
    )

    op.create_table(
        "workflow_task_dependencies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "depends_on_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
    )

    op.create_table(
        "approval_gates",
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
            "workflow_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gate_key", sa.Text(), nullable=False),
        sa.Column("gate_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("request_payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("decision_payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "gate_type IN ('requirement_review','compliance_review','resource_selection',"
            "'commercial_approval','finding_resolution','final_readiness')",
            name="ck_approval_gate_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected','changes_requested')",
            name="ck_approval_gate_status",
        ),
        sa.UniqueConstraint("workflow_run_id", "gate_key", name="uq_gate_key"),
    )

    # workflow_runs now exists - fulfills the deferred-FK comment left in
    # 001_initial.py on audit_events.workflow_run_id.
    op.create_foreign_key(
        "fk_audit_events_workflow_run_id",
        "audit_events",
        "workflow_runs",
        ["workflow_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_audit_events_workflow_run_id", "audit_events", type_="foreignkey")
    op.drop_table("approval_gates")
    op.drop_table("workflow_task_dependencies")
    op.drop_table("workflow_tasks")
    op.drop_table("workflow_runs")
