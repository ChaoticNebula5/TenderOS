from fastapi import APIRouter, Depends
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import auth_service

from tenderos_api.dependencies import get_current_user
from tenderos_api.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
)
from tenderos_api.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
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


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh(body: RefreshRequest) -> AccessTokenResponse:
    try:
        access_token = await auth_service.refresh_access_token(body.refresh_token)
    except auth_service.InvalidTokenError as exc:
        raise InvalidTokenError() from exc

    return AccessTokenResponse(access_token=access_token)


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    memberships = await auth_service.list_memberships(current_user.id)
    return MeResponse(
        user=UserResponse.model_validate(current_user, from_attributes=True),
        memberships=[
            MeMembership(organization_id=m.organization_id, role=m.role) for m in memberships
        ],
    )
