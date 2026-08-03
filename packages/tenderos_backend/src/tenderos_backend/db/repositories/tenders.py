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
        return tender

    async def bump_state_version(self, tender_id: uuid.UUID) -> None:
        """Increment tenders.state_version. Per Build Guide Part C1, callers are
        graph-relevant mutations (knowledge selection, evidence, compliance, role
        assignment, proposal claim, amendment impact changes) - none of which exist
        yet in B2. No real caller today; covered by a direct unit test only."""
        tender = await self._session.get(Tender, tender_id)
        if tender is None:
            raise ValueError(f"Tender {tender_id} not found")
        tender.state_version += 1
        await self._session.flush()
