"""outbox_events: indexes for the SSE stream's filter and for retention

Phase 4 plan item 55 (decision 0082). Every SSE poll read the whole
outbox_events table: the stream filters on a payload key that no index
covered (6,762 rows read per poll on the dev data, 2026-11-10). Two
expression indexes match the stream's filter exactly:

- idx_outbox_workflow_run_id on (payload ->> 'workflow_run_id'), the
  workflow-run stream;
- idx_outbox_operation_id on (payload ->> 'operation_id'), the operation
  stream.

The stream writes the key into its SQL as a literal from an allowlist
(tenderos_api/outbox_stream.py), because a key sent as a parameter cannot
use an expression index under a generic plan.

The third index, idx_outbox_dispatched_at on dispatched_at WHERE status =
'dispatched', exists only for retention: the dispatcher's cleanup finds the
oldest dispatched rows through it instead of scanning the table. The stream
never uses it. Dead letters are found through the existing
idx_outbox_pending (status, available_at).

CREATE INDEX CONCURRENTLY, so building them never blocks the outbox's writers
on a large table. It cannot run inside a transaction, hence the autocommit
block. If a concurrent build fails, Postgres leaves the index INVALID; drop
it by hand (DROP INDEX CONCURRENTLY) before running the migration again.

Downgrade drops the three indexes, concurrently too.

Revision ID: 026_outbox_indexes
Revises: 025_redteam_target_type
Create Date: 2026-11-10 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "026_outbox_indexes"
down_revision: str | None = "025_redteam_target_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "outbox_events"
_INDEXES = ("idx_outbox_workflow_run_id", "idx_outbox_operation_id", "idx_outbox_dispatched_at")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            "idx_outbox_workflow_run_id",
            _TABLE,
            [sa.text("(payload ->> 'workflow_run_id'::text)")],
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_outbox_operation_id",
            _TABLE,
            [sa.text("(payload ->> 'operation_id'::text)")],
            postgresql_concurrently=True,
        )
        op.create_index(
            "idx_outbox_dispatched_at",
            _TABLE,
            ["dispatched_at"],
            postgresql_where=sa.text("status = 'dispatched'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(_INDEXES):
            op.drop_index(name, table_name=_TABLE, postgresql_concurrently=True)
