"""requirements.verification_checked_at: when a requirement was last checked

Phase 4 plan item 59, Q5 (user decision 2026-11-19, option A). `verified`
alone made two states one false: a requirement never checked (every new
one, and every optional one, since the extraction node checks only the
mandatory ones) and one checked and not supported by its source. This
column, set from the database clock whenever a verdict is written, tells
them apart:
- not checked yet: verified false, no time;
- verification failed: verified false, a time;
- verified: verified true, a time.

ck_requirement_verified_has_check_time refuses the one illegal pair,
verified true with no time. A text edit sets both back (false, no time).

Upgrade, in one transaction:
1. The column, nullable, no default.
2. The backfill, from the verification traces: every verification call
   writes an agent_traces row (agent_name 'requirement_verification'), and
   since phase 4 it names the requirement in input_refs. Only completed
   calls count; a failed call wrote no verdict.
   - Verified rows: the latest completed trace naming the requirement, or
     the row's updated_at when there is none (a verdict from before traces
     named requirements).
   - Unverified rows with a completed trace: failed, at that trace's time.
     A completed trace whose verdict was refused because the text changed
     during the call (plan item 59, fix 6) counts too, so such a row reads
     failed at that trace's time, not "not checked yet". Possible only for
     traces written since PR 20 merged; accepted.
   - Unverified rows with none: not checked, left null. A false row checked
     before traces named requirements reads "not checked yet": that errs
     towards asking for a check, never towards hiding a failure.
3. The check constraint, once every verified row has a time.

Downgrade drops the constraint, then the column.

Revision ID: 035_requirement_check_time
Revises: 034_import_item_source_document
Create Date: 2026-11-19 00:00:00

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035_requirement_check_time"
down_revision: str | None = "034_import_item_source_document"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger("alembic.runtime.migration")

_CONSTRAINT = "ck_requirement_verified_has_check_time"

# Literal values, not imported from the code: a migration states the data as
# it was at its own revision. The agent name is verify.py's _AGENT_NAME.
BACKFILL_VERIFIED = sa.text(
    """
    UPDATE requirements AS r
    SET verification_checked_at = COALESCE(
        (
            SELECT max(t.created_at)
            FROM agent_traces AS t
            WHERE t.agent_name = 'requirement_verification'
              AND t.status = 'completed'
              AND t.input_refs->>'requirement_id' = r.id::text
        ),
        r.updated_at
    )
    WHERE r.verified
    """
)

BACKFILL_FAILED = sa.text(
    """
    UPDATE requirements AS r
    SET verification_checked_at = latest.checked_at
    FROM (
        SELECT t.input_refs->>'requirement_id' AS requirement_id,
               max(t.created_at) AS checked_at
        FROM agent_traces AS t
        WHERE t.agent_name = 'requirement_verification'
          AND t.status = 'completed'
          AND t.input_refs->>'requirement_id' IS NOT NULL
        GROUP BY 1
    ) AS latest
    WHERE NOT r.verified
      AND latest.requirement_id = r.id::text
    """
)


def upgrade() -> None:
    op.add_column(
        "requirements",
        sa.Column("verification_checked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    bind = op.get_bind()
    verified = bind.execute(BACKFILL_VERIFIED).rowcount
    failed = bind.execute(BACKFILL_FAILED).rowcount
    log.info(
        "verification_checked_at backfilled: %d verified, %d failed (the rest not checked yet)",
        verified,
        failed,
    )
    op.create_check_constraint(
        _CONSTRAINT, "requirements", "verified = false OR verification_checked_at IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "requirements", type_="check")
    op.drop_column("requirements", "verification_checked_at")
