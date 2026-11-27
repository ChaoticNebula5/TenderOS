"""readiness_reports.generation: the newest report by a sequence, not a clock

Plan item 120 (user decisions 2026-11-26, Q1 a).

"The latest report" was ordered by last_generated_at, the database's
clock_timestamp() at the insert or reuse, and that clock steps back (item
117, observed: 7 steps of up to 1.43 s in 90 s), so a report written after
another could sort before it. generation is taken from a database sequence
on insert (the column's default) and on every reuse (upsert_report's ON
CONFLICT update), and "latest" orders by it alone. The persist transaction
holds the tender's row lock around it (readiness_service), so one tender's
generations follow the order its reports were committed.

Upgrade:
1. creates readiness_report_generation_seq;
2. adds generation, nullable;
3. numbers the existing rows in today's order (last_generated_at, then id),
   so each tender's current report keeps the largest number among its own;
4. moves the sequence past the largest number;
5. sets generation NOT NULL with nextval as its default, and adds the
   unique key (tender_id, generation), which also serves "latest";
6. appends generation to the observability.readiness_reports view (CREATE
   OR REPLACE keeps the readers' grants), for the Work dashboard's
   newest-report-per-tender panel.

Downgrade puts the view back as it was (DROP and CREATE, since CREATE OR
REPLACE cannot remove a column; the migrate service grants it again on its
next run), then drops the key, the column and the sequence. last_generated_at
is still written, so the order returns to it.

Revision ID: 044_readiness_report_generation
Revises: 043_compliance_automated_answer
Create Date: 2026-11-26 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "044_readiness_report_generation"  # alembic_version holds 32 characters
down_revision: str | None = "043_compliance_automated_answer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SEQUENCE = "readiness_report_generation_seq"

_BACKFILL = """
UPDATE readiness_reports r
SET generation = numbered.n
FROM (
    SELECT id, row_number() OVER (ORDER BY last_generated_at, id) AS n
    FROM readiness_reports
) numbered
WHERE r.id = numbered.id
"""

# setval(seq, n, true) makes the next nextval n + 1; with no rows, setval(seq,
# 1, false) makes it 1.
_ADVANCE = f"""
SELECT CASE
    WHEN max(generation) IS NULL THEN setval('{_SEQUENCE}', 1, false)
    ELSE setval('{_SEQUENCE}', max(generation), true)
END
FROM readiness_reports
"""

_VIEW_BEFORE = (
    "SELECT tender_id, tender_state_version, ready, generated_at, last_generated_at, "
    'blockers @> \'[{"category": "stale_graph_projection"}]\'::jsonb AS stale_graph '
    "FROM public.readiness_reports"
)
_VIEW_AFTER = (
    "SELECT tender_id, tender_state_version, ready, generated_at, last_generated_at, "
    'blockers @> \'[{"category": "stale_graph_projection"}]\'::jsonb AS stale_graph, '
    "generation FROM public.readiness_reports"
)


def upgrade() -> None:
    op.execute(f"CREATE SEQUENCE {_SEQUENCE}")
    op.add_column("readiness_reports", sa.Column("generation", sa.BigInteger(), nullable=True))
    op.execute(_BACKFILL)
    op.execute(_ADVANCE)
    op.alter_column(
        "readiness_reports",
        "generation",
        nullable=False,
        server_default=sa.text(f"nextval('{_SEQUENCE}'::regclass)"),
    )
    op.create_unique_constraint(
        "uq_readiness_report_generation", "readiness_reports", ["tender_id", "generation"]
    )
    op.execute(f"CREATE OR REPLACE VIEW observability.readiness_reports AS {_VIEW_AFTER}")


def downgrade() -> None:
    op.execute("DROP VIEW observability.readiness_reports")
    op.execute(f"CREATE VIEW observability.readiness_reports AS {_VIEW_BEFORE}")
    op.drop_constraint("uq_readiness_report_generation", "readiness_reports", type_="unique")
    op.drop_column("readiness_reports", "generation")
    op.execute(f"DROP SEQUENCE {_SEQUENCE}")
