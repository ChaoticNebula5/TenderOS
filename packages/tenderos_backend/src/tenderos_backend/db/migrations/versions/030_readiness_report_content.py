"""readiness_reports: one report per content at a version, the newest current

Phase 4 plan item 93 (user decision 2026-11-15). Readiness used the bid's
state version as its cache key: a report stored at a version was returned
again for every later generate at that version. But readiness reads more than
the version counts (red-team findings, pricing findings, the graph's
projection, Neo4j's reachability, run state), so a stored report outlived
changes to those, for example "Graph projection is stale" after a
re-projection.

Readiness now computes on every generate and stores one row per distinct
content at a version: the unique key becomes (tender_id,
tender_state_version, content_hash), the hash being canonical JSON over
ready, blockers and counts (computed in readiness_service, not here). A
generate whose content already has a row reuses it and makes it the newest
through last_generated_at, so "the latest report" is ordered by that column
(ties by id), and content that goes X, Y, X makes X's row current again.

Upgrade, in one transaction:
1. content_hash: existing rows get 'legacy-' plus their id, a value no real
   hash can equal, so the next generate at their version appends a fresh
   report instead of reusing one computed under the old rules. The app's
   hash function is deliberately not imported here.
2. last_generated_at: existing rows get their generated_at.
3. uq_readiness_report_version (tender_id, tender_state_version) is
   replaced by uq_readiness_report_content (tender_id,
   tender_state_version, content_hash).

Downgrade refuses, with a message, when any (tender_id,
tender_state_version) has more than one report: restoring the old key would
mean deleting reports a waiver or a final gate's payload may point at.
Otherwise it restores the old key and drops both columns.

Revision ID: 030_readiness_report_content
Revises: 029_run_status_changes_requested
Create Date: 2026-11-15 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030_readiness_report_content"
down_revision: str | None = "029_run_status_changes_requested"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("readiness_reports", sa.Column("content_hash", sa.Text(), nullable=True))
    op.add_column(
        "readiness_reports",
        sa.Column("last_generated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE readiness_reports "
        "SET content_hash = 'legacy-' || id::text, last_generated_at = generated_at"
    )
    op.alter_column("readiness_reports", "content_hash", nullable=False)
    op.alter_column(
        "readiness_reports",
        "last_generated_at",
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.drop_constraint("uq_readiness_report_version", "readiness_reports", type_="unique")
    op.create_unique_constraint(
        "uq_readiness_report_content",
        "readiness_reports",
        ["tender_id", "tender_state_version", "content_hash"],
    )


def downgrade() -> None:
    duplicated = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM ("
                "SELECT 1 FROM readiness_reports "
                "GROUP BY tender_id, tender_state_version HAVING count(*) > 1"
                ") AS d"
            )
        )
        .scalar_one()
    )
    if duplicated:
        raise RuntimeError(
            f"refusing to downgrade 030_readiness_report_content: {duplicated} "
            "(tender_id, tender_state_version) pair(s) have more than one readiness "
            "report, and the old key allows one. Deleting reports is not done here: "
            "a waiver or a final gate's payload may point at any of them."
        )
    op.drop_constraint("uq_readiness_report_content", "readiness_reports", type_="unique")
    op.create_unique_constraint(
        "uq_readiness_report_version",
        "readiness_reports",
        ["tender_id", "tender_state_version"],
    )
    op.drop_column("readiness_reports", "last_generated_at")
    op.drop_column("readiness_reports", "content_hash")
