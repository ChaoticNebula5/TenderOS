import pytest
from tenderos_backend.core.enums import Role
from tenderos_backend.domain.services import auth_service, membership_service

pytestmark = pytest.mark.asyncio


async def test_add_membership_attaches_existing_user_to_another_org():
    admin = await auth_service.register(
        email="admin2@example.com", password="hunter2", organization_name="Org A"
    )
    bid_manager = await auth_service.register(
        email="bidmgr@example.com", password="hunter2", organization_name="Bid Manager's Own Org"
    )

    membership = await membership_service.add_membership(
        organization_id=admin.organization.id,
        email="bidmgr@example.com",
        role=Role.BID_MANAGER,
        actor_user_id=admin.user.id,
    )

    assert membership.organization_id == admin.organization.id
    assert membership.user_id == bid_manager.user.id
    assert membership.role == Role.BID_MANAGER.value


async def test_add_membership_rejects_unregistered_email():
    admin = await auth_service.register(
        email="admin3@example.com", password="hunter2", organization_name="Org C"
    )
    with pytest.raises(membership_service.UserNotFoundError):
        await membership_service.add_membership(
            organization_id=admin.organization.id,
            email="never-registered@example.com",
            role=Role.BID_MANAGER,
            actor_user_id=admin.user.id,
        )


async def test_add_membership_rejects_duplicate():
    admin = await auth_service.register(
        email="admin4@example.com", password="hunter2", organization_name="Org D"
    )
    other = await auth_service.register(
        email="member@example.com", password="hunter2", organization_name="Member's Own Org"
    )
    await membership_service.add_membership(
        organization_id=admin.organization.id,
        email=other.user.email,
        role=Role.BID_MANAGER,
        actor_user_id=admin.user.id,
    )
    with pytest.raises(membership_service.AlreadyMemberError):
        await membership_service.add_membership(
            organization_id=admin.organization.id,
            email=other.user.email,
            role=Role.SOLUTION_ARCHITECT,
            actor_user_id=admin.user.id,
        )
