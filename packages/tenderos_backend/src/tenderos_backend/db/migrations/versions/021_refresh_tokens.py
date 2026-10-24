"""refresh_tokens: server-side refresh-token rotation and revocation

Phase 4 plan item 34, option 3. Every issued refresh token gets a row,
keyed by the token's `jti`, holding a SHA-256 of the token (never the token)
and the family (one per login) it belongs to. A refresh marks the presented
token used and issues its successor; a used token presented again revokes
the whole family; logout revokes the family.

The partial unique index allows at most one live (unused, unrevoked) token
per family, so a bug or a race that would leave two live tokens fails its
insert instead.

Refresh tokens issued before this migration carry no `jti` and have no row;
they are refused, so every signed-in user logs in once.

Revision ID: 021_refresh_tokens
Revises: 020_async_operations
Create Date: 2026-10-24 00:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "021_refresh_tokens"
down_revision: str | None = "020_async_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "issued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.Text(), nullable=True),
        sa.Column(
            "replaced_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
        sa.CheckConstraint(
            "revoked_reason IN ('reuse','logout','user','superseded')",
            name="ck_refresh_token_revoked_reason",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name="ck_refresh_token_revoked_pair",
        ),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    # The referencing side of the self-referencing foreign key: each row
    # cleanup deletes makes ON DELETE SET NULL look up the rows pointing at
    # it, a whole-table scan per deleted row without this index.
    op.create_index("ix_refresh_tokens_replaced_by_id", "refresh_tokens", ["replaced_by_id"])
    op.create_index(
        "uq_refresh_token_live_per_family",
        "refresh_tokens",
        ["family_id"],
        unique=True,
        postgresql_where=sa.text("used_at IS NULL AND revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_refresh_token_live_per_family", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_replaced_by_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
