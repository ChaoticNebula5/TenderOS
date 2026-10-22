"""async_operations: long-running work requested over HTTP, run by the worker

Phase 4: no LLM call runs inside an HTTP request. A route that used to do
the work inline now records an async operation and returns 202 with its
id; the worker claims and runs it; the web app follows its status. The
table only tracks state (status, a safe error code and message,
timestamps, the worker's lease); results stay in each feature's own
tables.

The partial unique index allows at most one queued or running operation of
a kind per tender, so a second request while one is active returns the
existing operation instead of starting a second, concurrent run.

Revision ID: 020_async_operations
Revises: 019_idempotency_lease
Create Date: 2026-10-15 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "020_async_operations"
down_revision: str | None = "019_idempotency_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "async_operations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tender_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "kind IN ('redteam_run','proposal_generation','commercial_validation')",
            name="ck_async_operation_kind",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="ck_async_operation_status",
        ),
    )
    op.create_index(
        "uq_async_operation_active",
        "async_operations",
        ["tender_id", "kind"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued','running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_async_operation_active", table_name="async_operations")
    op.drop_table("async_operations")
