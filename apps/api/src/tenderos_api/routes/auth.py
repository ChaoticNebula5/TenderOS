from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from tenderos_backend.db.models.user import User
from tenderos_backend.domain.services import auth_service

from tenderos_api.dependencies import get_current_user
from tenderos_api.errors import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidTokenError,
    error_response,
)
from tenderos_api.schemas.auth import (
    LoginRequest,
    MeMembership,
    MeResponse,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from tenderos_api.session_cookie import (
    clear_refresh_cookie,
    read_refresh_cookie,
    require_web_client,
    set_refresh_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Plan item 34, option 2: the four routes that set or use the refresh cookie
# need the web client's header and origin (session_cookie.py). /auth/me and
# every other route take the bearer access token only.
_WEB_CLIENT_ONLY = [Depends(require_web_client)]


@router.post(
    "/register", response_model=RegisterResponse, status_code=201, dependencies=_WEB_CLIENT_ONLY
)
async def register(body: RegisterRequest, response: Response) -> RegisterResponse:
    try:
        result = await auth_service.register(
            email=body.email,
            password=body.password,
            organization_name=body.organization_name,
            display_name=body.display_name,
        )
    except auth_service.EmailAlreadyRegisteredError as exc:
        raise EmailAlreadyRegisteredError() from exc

    set_refresh_cookie(response, result.refresh_token)
    return RegisterResponse(
        user=UserResponse.model_validate(result.user, from_attributes=True),
        organization_id=result.organization.id,
        access_token=result.access_token,
    )


@router.post("/login", response_model=TokenResponse, dependencies=_WEB_CLIENT_ONLY)
async def login(body: LoginRequest, response: Response) -> TokenResponse:
    try:
        result = await auth_service.login(email=body.email, password=body.password)
    except auth_service.InvalidCredentialsError as exc:
        raise InvalidCredentialsError() from exc

    set_refresh_cookie(response, result.refresh_token)
    return TokenResponse(access_token=result.access_token)


# Every refresh rotates the refresh token (option 3): the cookie presented is
# spent and the answer sets its successor. A missing cookie is the same 401
# as an invalid one. A 401 also clears the cookie, so a dead one is not
# presented again; the error is built here rather than raised, because a
# raised APIError's response is made by the exception handler and would not
# carry this route's Set-Cookie.
@router.post("/refresh", response_model=TokenResponse, dependencies=_WEB_CLIENT_ONLY)
async def refresh(request: Request, response: Response) -> TokenResponse | JSONResponse:
    presented = read_refresh_cookie(request)
    try:
        if presented is None:
            raise auth_service.InvalidTokenError()
        tokens = await auth_service.refresh(presented)
    except auth_service.InvalidTokenError:
        rejected = error_response(InvalidTokenError())
        clear_refresh_cookie(rejected)
        return rejected

    set_refresh_cookie(response, tokens.refresh_token)
    return TokenResponse(access_token=tokens.access_token)


# No access token required: it may already have expired. 204 whatever the
# cookie was (missing, invalid, already revoked), so the answer tells a
# caller nothing, and the cookie is cleared every time. Revokes the refresh
# token's family only; access tokens already issued stay valid until they
# expire.
@router.post("/logout", status_code=204, response_class=Response, dependencies=_WEB_CLIENT_ONLY)
async def logout(request: Request) -> Response:
    presented = read_refresh_cookie(request)
    if presented is not None:
        await auth_service.logout(presented)
    answer = Response(status_code=204)
    clear_refresh_cookie(answer)
    return answer


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    memberships = await auth_service.list_memberships(current_user.id)
    return MeResponse(
        user=UserResponse.model_validate(current_user, from_attributes=True),
        memberships=[
            MeMembership(organization_id=m.organization_id, role=m.role) for m in memberships
        ],
    )
