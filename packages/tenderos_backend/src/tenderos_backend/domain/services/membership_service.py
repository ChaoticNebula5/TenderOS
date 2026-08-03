import uuid

from tenderos_backend.core.enums import Role
from tenderos_backend.db.models.membership import OrganizationMembership
from tenderos_backend.db.unit_of_work import UnitOfWork


class UserNotFoundError(Exception):
    pass


class AlreadyMemberError(Exception):
    pass


async def add_membership(
    organization_id: uuid.UUID, email: str, role: Role, actor_user_id: uuid.UUID
) -> OrganizationMembership:
    async with UnitOfWork() as uow:
        user = await uow.users.get_by_email(email)
        if user is None:
            raise UserNotFoundError(email)

        existing = await uow.memberships.get(organization_id, user.id)
        if existing is not None:
            raise AlreadyMemberError(email)

        membership = await uow.memberships.create(organization_id, user.id, role)
        await uow.audit.create(
            organization_id=organization_id,
            actor_type="user",
            actor_id=str(actor_user_id),
            action="add_membership",
            target_type="user",
            target_id=user.id,
            after={"role": role.value},
        )
        await uow.commit()
        return membership
