import uuid
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from tenderos_backend.db.models.base import Base, UUIDPrimaryKeyMixin


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """One issued refresh token (plan item 34, option 3; migration 021).

    id is the token's `jti` claim. A family is one login: every rotation
    stays in it, and reuse or logout revokes it whole. The token itself is
    never stored, only its SHA-256, so a database read or a backup yields no
    usable credential.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint(
            "revoked_reason IN ('reuse','logout','user','superseded')",
            name="ck_refresh_token_revoked_reason",
        ),
        CheckConstraint(
            "(revoked_at IS NULL) = (revoked_reason IS NULL)",
            name="ck_refresh_token_revoked_pair",
        ),
        # The invariant: at most one live token per family. Live means
        # neither used nor revoked; expiry is left out because now() cannot
        # appear in an index predicate, and an expired token is refused by
        # its JWT exp and removed by cleanup anyway. Rotation marks the
        # presented token used before it inserts the successor, so a code
        # path that ever forgot to fails its insert here instead of leaving
        # two. The grace path inserts nothing: it sends the existing
        # successor again (plan item 34, option 2). 'superseded' has had no
        # writer since then; the value stays for rows written before.
        Index(
            "uq_refresh_token_live_per_family",
            "family_id",
            unique=True,
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    # The family's expiry, copied to every member: rotation never extends a
    # session past 7 days from login.
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(nullable=True)
    # When the grace path handed this token out a second time (migration
    # 028; auth_service._grace_resend). A token handed out twice may be
    # rotated once; a re-send for it is refused as reuse, so a thief in
    # lockstep with the real client is caught at the next rotation.
    resent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # SET NULL: cleanup deletes expired rows in bounded batches, so a
    # family's rows can go in more than one batch. Indexed because of it:
    # Postgres does not index the referencing side of a foreign key, and
    # each deleted row's SET NULL looks up the rows pointing at it, so
    # without an index every deleted row would scan the whole table.
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
