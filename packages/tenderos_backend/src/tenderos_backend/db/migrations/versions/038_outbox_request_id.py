"""outbox_events: request_id

PR 30 (observability phase; decision Q1 a). The api request that wrote an
outbox event, so one request's log lines can be followed through the outbox
to the RQ job: the dispatcher hands the id to the job in its meta, and the
worker binds it (tenderos_backend.core.log_context).

A column, not a key in `payload`:
- the payload is the job's input, read by the dispatcher's route builders,
  and a log id does not belong in it;
- the read-only observability views decided with Q6 a leave `payload` out
  of their `outbox_events` view (it can hold user data); a column can be
  shown there and searched, without the payload;
- the database holds its shape: ck_outbox_request_id allows NULL or 32
  lowercase hex characters, the only shape the api binds
  (RequestIdMiddleware), so nothing else can be written through it.

Nullable, no default, no backfill: an event written outside an api request
(a worker's follow-up event outside any request, a script, the reaper) has
none, and older rows never had one. The check validates the existing rows,
all NULL. Downgrade drops the check, then the column.

Revision ID: 038_outbox_request_id
Revises: 037_agent_trace_owners_thinking
Create Date: 2026-11-21 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038_outbox_request_id"  # alembic_version holds 32 characters
down_revision: str | None = "037_agent_trace_owners_thinking"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("outbox_events", sa.Column("request_id", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_outbox_request_id",
        "outbox_events",
        "request_id IS NULL OR request_id ~ '^[0-9a-f]{32}$'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_outbox_request_id", "outbox_events", type_="check")
    op.drop_column("outbox_events", "request_id")
