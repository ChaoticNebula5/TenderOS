import uuid
from dataclasses import dataclass

import jwt

from tenderos_backend.core.enums import Role
from tenderos_backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from tenderos_backend.db.models.membership import OrganizationMembership
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.user import User
from tenderos_backend.db.unit_of_work import UnitOfWork


class EmailAlreadyRegisteredError(Exception):
    pass


class InvalidCredentialsError(Exception):
    pass


class InvalidTokenError(Exception):
    pass


@dataclass
class RegisterResult:
    user: User
    organization: Organization
    access_token: str
    refresh_token: str


@dataclass
class LoginResult:
    user: User
    access_token: str
    refresh_token: str


async def register(
    email: str, password: str, organization_name: str, display_name: str | None = None
) -> RegisterResult:
    async with UnitOfWork() as uow:
        existing = await uow.users.get_by_email(email)
        if existing is not None:
            raise EmailAlreadyRegisteredError(email)

        organization = await uow.organizations.create(name=organization_name)
        user = await uow.users.create(
            email=email, password_hash=hash_password(password), display_name=display_name
        )
        await uow.memberships.create(organization.id, user.id, Role.ADMIN)
        await uow.audit.create(
            organization_id=organization.id,
            actor_type="user",
            actor_id=str(user.id),
            action="register",
            target_type="user",
            target_id=user.id,
        )
        await uow.commit()

        return RegisterResult(
            user=user,
            organization=organization,
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
        )


async def login(email: str, password: str) -> LoginResult:
    async with UnitOfWork() as uow:
        user = await uow.users.get_by_email(email)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        return LoginResult(
            user=user,
            access_token=create_access_token(user.id),
            refresh_token=create_refresh_token(user.id),
        )


async def refresh_access_token(refresh_token: str) -> str:
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc

    if payload.get("token_type") != "refresh":
        raise InvalidTokenError()

    async with UnitOfWork() as uow:
        user = await uow.users.get(uuid.UUID(str(payload["user_id"])))
        if user is None or not user.is_active:
            raise InvalidTokenError()
        return create_access_token(user.id)


async def list_memberships(user_id: uuid.UUID) -> list[OrganizationMembership]:
    async with UnitOfWork() as uow:
        return await uow.memberships.list_for_user(user_id)
