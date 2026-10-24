import pytest
from tenderos_backend.core.enums import Role
from tenderos_backend.core.security import decode_token
from tenderos_backend.domain.services import auth_service

pytestmark = pytest.mark.asyncio


async def test_register_creates_user_org_and_admin_membership():
    result = await auth_service.register(
        email="admin@example.com", password="hunter2", organization_name="Acme Bidding"
    )

    assert result.user.email == "admin@example.com"
    assert result.organization.name == "Acme Bidding"

    memberships = await auth_service.list_memberships(result.user.id)
    assert len(memberships) == 1
    assert memberships[0].organization_id == result.organization.id
    assert memberships[0].role == Role.ADMIN.value


async def test_register_rejects_duplicate_email():
    await auth_service.register(
        email="dupe@example.com", password="hunter2", organization_name="Org One"
    )
    with pytest.raises(auth_service.EmailAlreadyRegisteredError):
        await auth_service.register(
            email="dupe@example.com", password="different", organization_name="Org Two"
        )


async def test_access_token_has_no_org_or_role_claims():
    result = await auth_service.register(
        email="claims@example.com", password="hunter2", organization_name="Claims Org"
    )
    payload = decode_token(result.access_token)
    assert payload["token_type"] == "access"
    assert "org_id" not in payload
    assert "role" not in payload


async def test_login_succeeds_with_correct_password():
    await auth_service.register(
        email="login@example.com", password="correct-horse", organization_name="Login Org"
    )
    result = await auth_service.login(email="login@example.com", password="correct-horse")
    assert result.user.email == "login@example.com"


async def test_login_rejects_wrong_password():
    await auth_service.register(
        email="wrongpw@example.com", password="correct-horse", organization_name="WrongPw Org"
    )
    with pytest.raises(auth_service.InvalidCredentialsError):
        await auth_service.login(email="wrongpw@example.com", password="incorrect")


async def test_login_rejects_unknown_email():
    with pytest.raises(auth_service.InvalidCredentialsError):
        await auth_service.login(email="nobody@example.com", password="whatever")


async def test_refresh_issues_new_access_token():
    result = await auth_service.register(
        email="refresh@example.com", password="hunter2", organization_name="Refresh Org"
    )
    pair = await auth_service.refresh(result.refresh_token)
    payload = decode_token(pair.access_token)
    assert payload["token_type"] == "access"
    assert payload["user_id"] == str(result.user.id)


async def test_refresh_rejects_an_access_token_used_as_refresh_token():
    result = await auth_service.register(
        email="misuse@example.com", password="hunter2", organization_name="Misuse Org"
    )
    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(result.access_token)
