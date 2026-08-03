from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from tenderos_backend.core.enums import Role
from tenderos_backend.core.security import decode_token
from tenderos_backend.db.models.user import User
from tenderos_backend.db.session import get_session as _get_backend_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.policies.rbac import ACTION_ROLES
from tenderos_backend.domain.services import tender_service

from tenderos_api.errors import OrgAccessDeniedError

_bearer_scheme = HTTPBearer()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with _get_backend_session() as session:
        yield session


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> User:
    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc

    if payload.get("token_type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not an access token")

    async with UnitOfWork() as uow:
        user = await uow.users.get(UUID(str(payload["user_id"])))
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
            )
        return user


async def _resolve_organization_id(request: Request) -> UUID:
    org_id = request.path_params.get("org_id")
    if org_id is not None:
        return UUID(org_id)

    tender_id = request.path_params.get("tender_id")
    if tender_id is not None:
        tender = await tender_service.get_tender(UUID(tender_id))
        if tender is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tender not found")
        return tender.organization_id

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="No organization context in path"
    )


def require_permission(action: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory: only lets the request through if the caller has a
    membership in the request's organization AND that membership's role is
    permitted for `action` per Part H's RBAC matrix (tenderos_backend.domain.
    policies.rbac.ACTION_ROLES).
    """

    async def _dependency(request: Request, current_user: User = Depends(get_current_user)) -> User:
        organization_id = await _resolve_organization_id(request)

        async with UnitOfWork() as uow:
            membership = await uow.memberships.get(organization_id, current_user.id)

            if membership is None:
                raise OrgAccessDeniedError()

            member_role = Role(membership.role)
            if member_role not in ACTION_ROLES[action]:
                raise OrgAccessDeniedError()

        return current_user

    return _dependency
