"""readiness: readiness_reports

Revision ID: 013_readiness
Revises: 012_redteam_resolution_source
Create Date: 2026-08-20 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "013_readiness"
down_revision: str | None = "012_redteam_resolution_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Part C, lines 1577-1586 - transcribed verbatim. UNIQUE(tender_id,
    # tender_state_version) PREVENTS a duplicate row for the same version -
    # it does not, by itself, make readiness_generation_node's own
    # re-generation idempotent (an earlier version of this comment claimed
    # it did; a real live run proved otherwise - see the CodeRabbit-review
    # fix in readiness_service.py). Two concurrent evaluate_readiness calls
    # at the same state_version both pass their own read-then-check, both
    # attempt an insert, and this constraint only lets ONE of them through -
    # the actual idempotency (the loser reading back the winner's row
    # instead of propagating the resulting IntegrityError) is application
    # code, in readiness_service.py's own except IntegrityError block, not
    # this constraint alone. Same "version instead of content hash" MERGE
    # spirit B11's finding fingerprints established, but MERGE-not-duplicate
    # still needed a real handler on the losing path, same as findings did.
    op.create_table(
        "readiness_reports",
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
        sa.Column("tender_state_version", sa.BigInteger(), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blockers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ready", sa.Boolean(), nullable=False),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tender_id", "tender_state_version", name="uq_readiness_report_version"
        ),
    )


def downgrade() -> None:
    op.drop_table("readiness_reports")
