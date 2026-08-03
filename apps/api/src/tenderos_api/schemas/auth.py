from uuid import UUID

from pydantic import BaseModel, EmailStr


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    organization_name: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: UUID
    email: str
    display_name: str | None
    is_active: bool


class RegisterResponse(BaseModel):
    user: UserResponse
    organization_id: UUID
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MeMembership(BaseModel):
    organization_id: UUID
    role: str


class MeResponse(BaseModel):
    user: UserResponse
    memberships: list[MeMembership]
