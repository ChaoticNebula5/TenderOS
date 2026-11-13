"""refresh_tokens.resent_at: when the grace path handed a token out a second time

Phase 4 plan item 34, option 2, review. The grace path sends a used token's
unused successor again (two tabs refreshing at once, or a lost response
retried). That made a thief who replayed a token within the window hold the
same successor as the real client; if both then refreshed within the window
of each other at every rotation, reuse detection never fired.

resent_at marks a successor the grace path handed out again. The grace path
refuses to re-send for a presented token whose own resent_at is set: that is
reuse, and the family is revoked. A re-sent token can still be rotated
normally once; it is a second simultaneous presentation of it that is refused.

Nullable, no default, no backfill: every existing row reads as never re-sent.
Nothing recorded re-sends before this, and migration 027 has already revoked
every live token, so no existing row can reach the grace path's check live.

Downgrade drops the column.

Revision ID: 028_refresh_token_resent_at
Revises: 027_revoke_live_refresh_tokens
Create Date: 2026-11-13 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "028_refresh_token_resent_at"
down_revision: str | None = "027_revoke_live_refresh_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "refresh_tokens",
        sa.Column("resent_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("refresh_tokens", "resent_at")
