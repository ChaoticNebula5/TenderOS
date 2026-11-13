"""refresh_tokens: revoke every live token, once, for the move to a cookie

Phase 4 plan item 34, option 2. The refresh token moves from the web app's
localStorage into an HttpOnly cookie. Every token issued before this release
was readable by page script; a copy taken then would keep working, sent as a
cookie, until its family expired (up to 7 days). So every live token (neither
used nor revoked) is revoked here, with the existing reason 'user', and every
browser signs in once. Nobody is deployed, so only development sessions are
affected.

Used tokens, tokens already revoked (reuse, logout, superseded) and expired
tokens are left as they are: only a live token can be refreshed, and cleanup
deletes the rest at their expiry. revoked_at is the database clock, as every
other revocation is (decision 0030).

No schema change: 'user' is already allowed by ck_refresh_token_revoked_reason
(migration 021) and had no writer until this.

Downgrade does nothing. A revocation is not undone: nothing records which
rows this migration revoked rather than a later logout or reuse.

Revision ID: 027_revoke_live_refresh_tokens
Revises: 026_outbox_indexes
Create Date: 2026-11-13 00:00:00

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027_revoke_live_refresh_tokens"
down_revision: str | None = "026_outbox_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Alembic's own logger: alembic.ini sends it to the console at INFO, so this
# reaches `alembic upgrade`'s output, and the migrate container's log.
log = logging.getLogger("alembic.runtime.migration")

REVOKE_LIVE = sa.text(
    "UPDATE refresh_tokens SET revoked_at = now(), revoked_reason = 'user' "
    "WHERE used_at IS NULL AND revoked_at IS NULL AND expires_at > now()"
)


def upgrade() -> None:
    revoked = op.get_bind().execute(REVOKE_LIVE).rowcount
    log.info("revoked %d live refresh token(s) for the move to the session cookie", revoked)


def downgrade() -> None:
    pass
