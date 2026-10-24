from fastapi import APIRouter, Depends, Response
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import auth_service

from tenderos_api.dependencies import get_current_user
from tenderos_api.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from tenderos_api.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MeMembership,
    MeResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(body: RegisterRequest) -> RegisterResponse:
    try:
        result = await auth_service.register(
            email=body.email,
            password=body.password,
            organization_name=body.organization_name,
            display_name=body.display_name,
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise EmailAlreadyRegisteredError() from exc

    return RegisterResponse(
        user=UserResponse.model_validate(result.user, from_attributes=True),
        organization_id=result.organization.id,
        access_token=result.access_token,
        refresh_token=result.refresh_token,
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    try:
        result = await auth_service.login(email=body.email, password=body.password)
    except auth_service.InvalidCredentialsError as exc:
        raise InvalidCredentialsError() from exc

    return TokenResponse(access_token=result.access_token, refresh_token=result.refresh_token)


# Plan item 34, option 3: every refresh rotates the refresh token, so the
# answer carries both tokens and the client stores both. The presented
# refresh token is spent by this call.
@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest) -> TokenResponse:
    try:
        tokens = await auth_service.refresh(body.refresh_token)
    except auth_service.InvalidTokenError as exc:
        raise InvalidTokenError() from exc

    return TokenResponse(access_token=tokens.access_token, refresh_token=tokens.refresh_token)


# No access token required: it may already have expired. 204 whatever the
# token was (unknown, invalid, already revoked), so the answer tells a
# caller nothing. Revokes the refresh token's family only; access tokens
# already issued stay valid until they expire.
@router.post("/logout", status_code=204, response_class=Response)
async def logout(body: LogoutRequest) -> Response:
    await auth_service.logout(body.refresh_token)
    return Response(status_code=204)


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    memberships = await auth_service.list_memberships(current_user.id)
    return MeResponse(
        user=UserResponse.model_validate(current_user, from_attributes=True),
        memberships=[
            MeMembership(organization_id=m.organization_id, role=m.role) for m in memberships
        ],
    )
