"""redteam: redteam_findings.resolution_source

Revision ID: 012_redteam_resolution_source
Revises: 011_redteam
Create Date: 2026-08-19 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012_redteam_resolution_source"
down_revision: str | None = "011_redteam"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The exact system-authored note auto_retire_undetected_findings has always
# written (redteam_service.py) - used once, here, to backfill existing rows.
# Not a runtime identification mechanism (that's what this migration adds a
# real column to replace) - a one-time historical inference for data that
# predates resolution_source existing at all.
_AUTO_RETIRE_NOTE = "Auto-retired: no longer detected by the most recent red-team pass."


def upgrade() -> None:
    # NULL for unresolved rows (resolution_source only has meaning once
    # resolved=true) - a human resolution sets 'human'
    # (RedteamRepository.resolve_finding), an auto-retirement sets 'system'
    # (RedteamRepository.auto_retire_undetected_findings). Distinguishing
    # the two is what lets _persist_draft reopen a system-retired finding
    # that reappears while still leaving a human's resolution alone - a
    # column, not a resolution_note string match, so the two write sites
    # can never drift out of sync with each other or with a reader.
    op.add_column(
        "redteam_findings",
        sa.Column("resolution_source", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_redteam_finding_resolution_source",
        "redteam_findings",
        "resolution_source IN ('system','human')",
    )

    # Backfill: every row resolved before this column existed. The literal
    # note match identifies the ones this codebase's own auto-retirement
    # wrote; everything else already resolved is presumed human (the only
    # other write path to resolved=true, resolve_finding, predates this
    # migration too).
    op.execute(
        sa.text(
            """
            UPDATE redteam_findings
            SET resolution_source = CASE
                WHEN resolution_note = :auto_note THEN 'system'
                ELSE 'human'
            END
            WHERE resolved = true
            """
        ).bindparams(auto_note=_AUTO_RETIRE_NOTE)
    )


def downgrade() -> None:
    op.drop_constraint("ck_redteam_finding_resolution_source", "redteam_findings", type_="check")
    op.drop_column("redteam_findings", "resolution_source")
