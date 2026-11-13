import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.refresh_token import RefreshToken

# Plan item 34, option 3. Like IdempotencyRepository, every time this
# repository writes or compares comes from the database clock (func.now(),
# decision 0030), except expires_at, which is the same value as the JWT's
# own `exp` and is passed in. Nothing here commits; auth_service owns the
# transaction, because a rotation is several statements that must land
# together.

# The first key of the two-key advisory lock, so these locks cannot collide
# with any other advisory lock taken in this database. Arbitrary; it only
# has to stay the same.
_FAMILY_LOCK_CLASS = 3401


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        token_id: uuid.UUID,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> RefreshToken:
        """Insert a live token. Raises IntegrityError
        (uq_refresh_token_live_per_family) if the family already has one."""
        result = await self._session.execute(
            insert(RefreshToken)
            .values(
                id=token_id,
                user_id=user_id,
                family_id=family_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
            .returning(RefreshToken)
        )
        return result.scalar_one()

    async def family_of(self, token_id: uuid.UUID) -> uuid.UUID | None:
        """The family a token belongs to, read without a lock. Safe unlocked
        because a row's family_id never changes; the caller locks the family
        next and reads the row again under that lock."""
        result = await self._session.execute(
            select(RefreshToken.family_id).where(RefreshToken.id == token_id)
        )
        return result.scalar_one_or_none()

    async def lock_family(self, family_id: uuid.UUID) -> None:
        """Take the family's transaction-scoped advisory lock; it is released
        at commit or rollback.

        Every refresh and logout takes it before touching a family's rows,
        so all of a family's changes run one at a time. A row lock on the
        presented token alone would serialise two requests with the same
        token, but not two with different tokens of one family (a replayed
        old token and the real client's current one): both would then
        revoke the whole family, lock its rows in different orders, and one
        would fail with a deadlock. hashtext() can map two families to one
        key; they then wait on each other briefly, which is harmless."""
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_class, hashtext(:family_id))"),
            {"lock_class": _FAMILY_LOCK_CLASS, "family_id": str(family_id)},
        )

    async def get_for_update(self, token_id: uuid.UUID) -> RefreshToken | None:
        """The token's row, locked until the transaction ends. populate_
        existing so a row this session read before is refreshed from the
        database, not served from the identity map."""
        result = await self._session.execute(
            select(RefreshToken)
            .where(RefreshToken.id == token_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def used_within(self, token_id: uuid.UUID, window: timedelta) -> bool:
        """Whether the token was used less than `window` ago, on the
        database clock (used_at was written by now() too)."""
        result = await self._session.execute(
            select(RefreshToken.id).where(
                RefreshToken.id == token_id,
                RefreshToken.used_at > func.now() - window,
            )
        )
        return result.scalar_one_or_none() is not None

    async def mark_used(self, token_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(used_at=func.now())
            .execution_options(synchronize_session=False)
        )

    async def set_replaced_by(self, token_id: uuid.UUID, successor_id: uuid.UUID) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(replaced_by_id=successor_id)
            .execution_options(synchronize_session=False)
        )

    async def mark_resent(self, token_id: uuid.UUID) -> None:
        """Record that the grace path handed this token out again. The first
        time only: a later re-send keeps the first stamp (COALESCE)."""
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(resent_at=func.coalesce(RefreshToken.resent_at, func.now()))
            .execution_options(synchronize_session=False)
        )

    async def revoke_family(self, family_id: uuid.UUID, reason: str) -> int:
        """Revoke every member of the family not already revoked; returns how
        many. Members already revoked (an earlier logout, or a token
        superseded before plan item 34's option 2) keep their first reason."""
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=func.now(), revoked_reason=reason)
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())

    async def delete_expired(self, batch_size: int) -> int:
        """Delete at most batch_size tokens whose expires_at has passed
        (database clock), oldest first, and return how many were deleted.

        The same shape as IdempotencyRepository.delete_expired, for the same
        reasons: one bounded batch per call, SKIP LOCKED so two callers never
        block each other, and a MATERIALIZED CTE, because the planner can
        re-run a `LIMIT ... FOR UPDATE` subquery once per outer row and
        delete more than the limit (that method's comment has the
        measurement). An expired row has nothing left to detect: its JWT is
        expired too, so it is refused before any row is read.
        """
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        expired_batch = (
            select(RefreshToken.id)
            .where(RefreshToken.expires_at < func.now())
            .order_by(RefreshToken.expires_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
            .cte("expired_batch")
            .prefix_with("MATERIALIZED")
        )
        result = await self._session.execute(
            delete(RefreshToken)
            .where(RefreshToken.id.in_(select(expired_batch.c.id)))
            .returning(RefreshToken.id)
            .execution_options(synchronize_session=False)
        )
        return len(result.scalars().all())
