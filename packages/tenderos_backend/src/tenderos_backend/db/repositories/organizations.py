import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.db.models.organization import Organization


class OrganizationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, name: str) -> Organization:
        organization = Organization(name=name)
        self._session.add(organization)
        await self._session.flush()
        return organization

    async def get(self, organization_id: uuid.UUID) -> Organization | None:
        return await self._session.get(Organization, organization_id)
