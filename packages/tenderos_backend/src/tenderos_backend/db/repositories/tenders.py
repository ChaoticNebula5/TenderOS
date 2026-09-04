import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.tender import Tender


class TenderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        """Increment tenders.state_version. Per Build Guide Part C1, callers are
        graph-relevant mutations (knowledge selection, evidence, compliance, role
        assignment, proposal claim, amendment impact changes).

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
        commits, instead of racing it.
        """
        tender = await self._session.get(Tender, tender_id, with_for_update=True)
        if tender is None:
            raise ValueError(f"Tender {tender_id} not found")
        tender.state_version += 1
        await self._session.flush()
