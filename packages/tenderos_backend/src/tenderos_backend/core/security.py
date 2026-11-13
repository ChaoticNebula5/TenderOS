import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from tenderos_backend.core.config import settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _create_token(user_id: UUID, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(UTC)
    payload = {
        "user_id": str(user_id),
        "token_type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, settings.jwt_signing_key, algorithm="HS256")


def create_access_token(user_id: UUID) -> str:
    return _create_token(user_id, "access", timedelta(minutes=settings.jwt_access_minutes))


def refresh_family_expiry() -> datetime:
    """When a new login's refresh-token family expires. Every token rotated
    from it keeps this expiry (plan item 34, option 3), so a session ends 7
    days after login however active it is: an absolute limit, as before."""
    return datetime.now(UTC) + timedelta(days=settings.jwt_refresh_days)


def create_refresh_token(user_id: UUID, token_id: UUID, expires_at: datetime) -> str:
    """A refresh token whose `jti` is its refresh_tokens row id. `exp` is
    the family's expiry, passed in, not a fresh 7 days.

    No `iat` (plan item 34, option 2): every claim comes from the token's
    row, and HS256 is deterministic, so the same row always mints the same
    string. The grace path relies on that to send a retried request the
    successor it already issued (auth_service._grace_resend)."""
    payload = {
        "user_id": str(user_id),
        "token_type": "refresh",
        "jti": str(token_id),
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.jwt_signing_key, algorithm="HS256")


def hash_refresh_token(token: str) -> str:
    """SHA-256 (hex) of the whole token string, the only form stored. A fast
    hash is enough: the input is a signed JWT, not a password, so there is
    nothing short to brute-force."""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_token(token: str) -> dict[str, object]:
    return jwt.decode(token, settings.jwt_signing_key, algorithms=["HS256"])
