import uuid
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim

# Plan item 18 (decision 0036). Every time written or compared here comes
# from the database clock (func.now(), decision 0030), like
# IdempotencyRepository. Nothing here commits: reanalysis_service owns the
# transactions, and a claim must commit on its own before the job does any
# work, so the unique key's lock is held only for that short insert.


class ReanalysisClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        tender_id: uuid.UUID,
        knowledge_item_id: uuid.UUID,
        state_version: int,
        lease: timedelta,
    ) -> uuid.UUID | None:
        """Insert the claim for this selection change and return its fresh
        claim_token, or None if the change is already claimed.

        One INSERT ... ON CONFLICT DO NOTHING on uq_reanalysis_claim. When
        two deliveries insert at once, Postgres makes the second wait on the
        first's uncommitted index entry, then finds the conflict and inserts
        nothing, so exactly one gets a token back."""
        token = uuid.uuid4()
        result = await self._session.execute(
            insert(ReanalysisClaim)
            .values(
                tender_id=tender_id,
                knowledge_item_id=knowledge_item_id,
                state_version=state_version,
                status="running",
                claim_token=token,
                locked_until=func.now() + lease,
            )
            .on_conflict_do_nothing(constraint="uq_reanalysis_claim")
            .returning(ReanalysisClaim.claim_token)
        )
        return result.scalar_one_or_none()

    async def take_over_expired(
        self,
        tender_id: uuid.UUID,
        knowledge_item_id: uuid.UUID,
        state_version: int,
        lease: timedelta,
    ) -> uuid.UUID | None:
        """Give the caller a running claim whose lease has lapsed (its job
        died or was killed): a fresh claim_token and a new lease. Returns the
        token, or None if nothing matched (completed, or a live lease).

        The lease check sits inside this one UPDATE, as in
        IdempotencyRepository.take_over_expired: of two takers, the second
        waits on the row lock, re-checks its WHERE against the row the first
        wrote, finds a live lease and matches nothing."""
        token = uuid.uuid4()
        result = await self._session.execute(
            update(ReanalysisClaim)
            .where(
                ReanalysisClaim.tender_id == tender_id,
                ReanalysisClaim.knowledge_item_id == knowledge_item_id,
                ReanalysisClaim.state_version == state_version,
                ReanalysisClaim.status == "running",
                ReanalysisClaim.locked_until < func.now(),
            )
            .values(claim_token=token, locked_until=func.now() + lease)
            .returning(ReanalysisClaim.claim_token)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none()

    async def get_with_lease(
        self, tender_id: uuid.UUID, knowledge_item_id: uuid.UUID, state_version: int
    ) -> tuple[ReanalysisClaim, bool] | None:
        """The claim for this selection change, if any, and whether its lease
        is still live on the database clock (plan item 20: the re-drive tool
        reports what the job's own claim would do, without claiming)."""
        result = await self._session.execute(
            select(ReanalysisClaim, ReanalysisClaim.locked_until >= func.now()).where(
                ReanalysisClaim.tender_id == tender_id,
                ReanalysisClaim.knowledge_item_id == knowledge_item_id,
                ReanalysisClaim.state_version == state_version,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        claim, lease_live = row
        return claim, bool(lease_live)

    async def complete(self, claim_token: uuid.UUID) -> bool:
        """Mark the claim completed, only while the caller's token is still
        the current one (its lease may have lapsed and been taken over).
        Returns whether it was marked."""
        result = await self._session.execute(
            update(ReanalysisClaim)
            .where(
                ReanalysisClaim.claim_token == claim_token,
                ReanalysisClaim.status == "running",
            )
            .values(status="completed", completed_at=func.now())
            .returning(ReanalysisClaim.id)
            .execution_options(synchronize_session=False)
        )
        return result.scalar_one_or_none() is not None
