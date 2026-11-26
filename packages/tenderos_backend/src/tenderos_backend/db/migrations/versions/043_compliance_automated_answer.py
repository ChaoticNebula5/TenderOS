"""compliance_entries: the analysis's own answer beside the decision in effect

Plan item 121 (user decisions 2026-11-26, Q1 a, Q3 a).

A re-evaluation compared its new answer with the row's status, and after an
override that status is the reviewer's, so the analysis repeating the very
answer the reviewer overrode counted as a change: the override and its
approval were undone. Three columns let it compare the analysis with itself:
- automated_status: the status the last evaluation stored, after the
  evidence-sufficiency check (the status CHECK's values);
- automated_reasoning_summary: the analysis's reasoning, so a re-evaluation
  has somewhere to write it without touching a reviewer's;
- decided_by: whose values status and reasoning_summary hold, 'analysis' or
  'reviewer'.

The backfill. Every re-evaluation writes the status, so after an entry's
last evaluation only an override changes it. An entry with a
compliance_override audit event later than its last_evaluated_at was decided
by a reviewer, and the earliest such event's `before` is what the analysis
had left; every other entry's status and reasoning are the analysis's own.
last_evaluated_at comes from the worker's clock and the audit's created_at
from the database's, so an override and an evaluation of one entry less than
a clock step apart could be misordered; either way the result is today's
behaviour for that entry once, or its decision asked again once.

Upgrade adds the columns (automated_status nullable until the backfill),
backfills, then sets automated_status NOT NULL and adds the checks.
Downgrade drops them in reverse; status keeps the decision in effect.

Revision ID: 043_compliance_automated_answer
Revises: 042_agent_trace_rate_limits
Create Date: 2026-11-26 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "043_compliance_automated_answer"  # alembic_version holds 32 characters
down_revision: str | None = "042_agent_trace_rate_limits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'fully_compliant','partially_compliant','non_compliant',"
    "'clarification_required','evidence_unavailable','not_reviewed'"
)

# The earliest override since each entry's last evaluation. `before` is
# written by override_compliance_entry with status and reasoning_summary
# always present; `#>> '{}'` reads a JSON string as text and a JSON null as
# NULL. COALESCE keeps the row's own status should a `before` lack one.
_BACKFILL_REVIEWER = """
WITH first_override AS (
    SELECT DISTINCT ON (a.target_id)
        a.target_id AS entry_id,
        a.before ->> 'status' AS status,
        (a.before -> 'reasoning_summary') #>> '{}' AS reasoning_summary
    FROM audit_events a
    JOIN compliance_entries ce ON ce.id = a.target_id
    WHERE a.action = 'compliance_override'
      AND a.target_type = 'compliance_entry'
      AND a.created_at > ce.last_evaluated_at
    ORDER BY a.target_id, a.created_at, a.id
)
UPDATE compliance_entries ce
SET decided_by = 'reviewer',
    automated_status = COALESCE(fo.status, ce.status),
    automated_reasoning_summary = fo.reasoning_summary
FROM first_override fo
WHERE ce.id = fo.entry_id
"""

_BACKFILL_ANALYSIS = """
UPDATE compliance_entries
SET automated_status = status,
    automated_reasoning_summary = reasoning_summary
WHERE automated_status IS NULL
"""


def upgrade() -> None:
    op.add_column("compliance_entries", sa.Column("automated_status", sa.Text(), nullable=True))
    op.add_column(
        "compliance_entries", sa.Column("automated_reasoning_summary", sa.Text(), nullable=True)
    )
    op.add_column(
        "compliance_entries",
        sa.Column("decided_by", sa.Text(), nullable=False, server_default=sa.text("'analysis'")),
    )
    op.execute(_BACKFILL_REVIEWER)
    op.execute(_BACKFILL_ANALYSIS)
    op.alter_column("compliance_entries", "automated_status", nullable=False)
    op.create_check_constraint(
        "ck_compliance_entry_automated_status",
        "compliance_entries",
        f"automated_status IN ({_STATUSES})",
    )
    op.create_check_constraint(
        "ck_compliance_entry_decided_by",
        "compliance_entries",
        "decided_by IN ('analysis','reviewer')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_compliance_entry_decided_by", "compliance_entries", type_="check")
    op.drop_constraint("ck_compliance_entry_automated_status", "compliance_entries", type_="check")
    op.drop_column("compliance_entries", "decided_by")
    op.drop_column("compliance_entries", "automated_reasoning_summary")
    op.drop_column("compliance_entries", "automated_status")
