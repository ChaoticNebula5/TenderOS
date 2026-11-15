import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.tender import Tender


class TenderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        # Tenders whose state_version this transaction has already moved
        # (plan item 95). A repository lives for one UnitOfWork, and
        # UnitOfWork.commit()/rollback() clear this, so it means "in this
        # transaction".
        self._bumped_in_transaction: set[uuid.UUID] = set()

    async def create(
        self,
        organization_id: uuid.UUID,
        name: str,
        client_name: str | None = None,
        submission_date: date | None = None,
        created_by: uuid.UUID | None = None,
    ) -> Tender:
        tender = Tender(
            organization_id=organization_id,
            name=name,
            client_name=client_name,
            submission_date=submission_date,
            created_by=created_by,
        )
        self._session.add(tender)
        await self._session.flush()
        return tender

    async def get(self, tender_id: uuid.UUID) -> Tender | None:
        return await self._session.get(Tender, tender_id)

    async def list_for_organization(self, organization_id: uuid.UUID) -> list[Tender]:
        result = await self._session.execute(
            select(Tender).where(Tender.organization_id == organization_id)
        )
        return list(result.scalars().all())

    async def update(
        self,
        tender_id: uuid.UUID,
        name: str | None = None,
        client_name: str | None = None,
        submission_date: date | None = None,
        status: str | None = None,
    ) -> Tender | None:
        tender = await self._session.get(Tender, tender_id)
        if tender is None:
            return None
        if name is not None:
            tender.name = name
        if client_name is not None:
            tender.client_name = client_name
        if submission_date is not None:
            tender.submission_date = submission_date
        if status is not None:
            tender.status = status
        await self._session.flush()
        # updated_at is server-side (onupdate=func.now()) - flush() alone
        # does not populate it back onto the in-memory object for an
        # UPDATE the way it does for an INSERT's RETURNING. Without this
        # refresh, the caller (tender_service.update_tender, then
        # routes/tenders.py serializing TenderResponse *after* this
        # UnitOfWork's session has closed) hits DetachedInstanceError
        # trying to lazily load updated_at from a session that's gone -
        # never caught before because no test exercised this route's real
        # HTTP response serialization, only the service function directly.
        await self._session.refresh(tender)
        return tender

    async def bump_state_version(self, tender_id: uuid.UUID) -> None:
        """Mark the tender's graph-relevant state as changed: tenders.state_version
        moves by one, once per transaction (plan item 95).

        The version says "something the graph shows changed since the last
        projection", so one transaction that writes many projected rows (an
        extraction, a batch of gate approvals, a merge) needs one step, not
        one per row. A second call for the same tender in the same
        transaction does nothing. After commit() or rollback() the next call
        moves it again.

        Callers bump when they change something the projector writes
        (graph_projection/projector.py), and only then; a write of data the
        graph does not show must not make the graph stale.
        tests/unit/test_projection_bump_guard.py checks every writer of
        projected data calls this (or is allowlisted with a reason).
        """
        if tender_id in self._bumped_in_transaction:
            return
        await self.increment_state_version(tender_id)

    async def increment_state_version(self, tender_id: uuid.UUID) -> None:
        """Move tenders.state_version by one on every call, for a caller that
        uses the new version as an event's identity: set_knowledge_selection
        keys its outbox event and the reanalysis job on it, so two selection
        changes in one transaction must get two versions (plan item 95).
        Everything else calls bump_state_version.

        B16: `with_for_update=True` - this used to be a plain SELECT (no lock)
        + an in-Python `+= 1` + flush, a classic lost-update race under real
        concurrency (reproduced live, 10 genuinely concurrent writers,
        tests/integration/test_state_version.py). `SELECT ... FOR UPDATE`,
        not an atomic `UPDATE ... SET state_version = state_version + 1
        RETURNING`, deliberately: knowledge_service.py's own
        set_knowledge_selection (its own comment, right after this call)
        relies on the SAME identity-mapped Tender instance already
        reflecting the increment with no extra query - a Core-level
        RETURNING update bypasses the ORM's identity map and would leave
        that caller reading a stale value, a real regression a theoretically
        cleaner fix would have introduced silently. FOR UPDATE keeps the
        exact same "load the ORM object, mutate the attribute, flush" shape
        every caller already depends on; it just makes a second concurrent
        SELECT ... FOR UPDATE for the same row block until this transaction
        commits, instead of racing it. The lock is held until commit, so
        callers bump after their other writes.
        """
        tender = await self._session.get(Tender, tender_id, with_for_update=True)
        if tender is None:
            raise ValueError(f"Tender {tender_id} not found")
        tender.state_version += 1
        await self._session.flush()
        self._bumped_in_transaction.add(tender_id)

    def forget_bumps(self) -> None:
        """Called by UnitOfWork.commit() and rollback(): the transaction the
        bumps belonged to is over."""
        self._bumped_in_transaction.clear()
