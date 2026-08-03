import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.membership import OrganizationMembership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, organization_id: uuid.UUID, user_id: uuid.UUID, role: Role
    ) -> OrganizationMembership:
        membership = OrganizationMembership(
            organization_id=organization_id, user_id=user_id, role=role.value
        )
        self._session.add(membership)
        await self._session.flush()
        return membership

    async def get(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        result = await self._session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationMembership]:
        result = await self._session.execute(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
        )
        return list(result.scalars().all())
