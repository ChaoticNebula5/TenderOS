"""idempotency_records: lease columns (locked_until, claim_token)

Phase 4 blocker 2. An in_progress idempotency record used to stay
in_progress forever if its request died before finalize(), so every retry
with that key got 409 for good. These two columns give the record a lease:
locked_until is when the holder's claim lapses, claim_token says who holds
it. A retry may take over a record whose lease has lapsed.

Both columns are nullable and added without a default, so existing rows
get NULL in both. The application treats locked_until NULL as an expired
lease, so a key created before this migration can be taken over rather
than being stranded.

Revision ID: 019_idempotency_lease
Revises: 018_approval_gate_cost_budget
Create Date: 2026-10-13 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "019_idempotency_lease"
down_revision: str | None = "018_approval_gate_cost_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "idempotency_records",
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("idempotency_records", "claim_token")
    op.drop_column("idempotency_records", "locked_until")
