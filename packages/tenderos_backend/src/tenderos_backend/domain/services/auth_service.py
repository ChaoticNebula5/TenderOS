import hmac
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import jwt

from tenderos_backend.core.enums import Role
from tenderos_backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    refresh_family_expiry,
    verify_password,
)
from tenderos_backend.db.models.membership import OrganizationMembership
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.refresh_token import RefreshToken
from tenderos_backend.db.models.user import User
from tenderos_backend.db.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

# Plan item 34, option 3: a used refresh token presented again this soon
# after its rotation, whose successor has never been used, is taken as the
# same client retrying (a lost response, or two tabs refreshing at once),
# not as theft. A design number, not measured.
#
# Option 2 (the cookie): the retry gets the same successor back, not a new
# one (_grace_resend), so every answer to requests carrying one token sets
# the same cookie, and the browser's jar ends on a live token whatever
# order it processes them in. A thief replaying within the window gets the
# token the real client holds; whichever of them uses it second, past the
# window, is reuse, so the family is still revoked, one rotation later.
REFRESH_REUSE_GRACE = timedelta(seconds=30)


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


@dataclass
class TokenPair:
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
        _, refresh_token = await _issue_refresh_token(
            uow, user.id, uuid.uuid4(), refresh_family_expiry()
        )
        await uow.commit()

        return RegisterResult(
            user=user,
            organization=organization,
            access_token=create_access_token(user.id),
            refresh_token=refresh_token,
        )


async def login(email: str, password: str) -> LoginResult:
    async with UnitOfWork() as uow:
        user = await uow.users.get_by_email(email)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError()

        # Every login starts a new family; other sessions of the same user
        # (another browser, another device) keep their own.
        _, refresh_token = await _issue_refresh_token(
            uow, user.id, uuid.uuid4(), refresh_family_expiry()
        )
        await uow.commit()

        return LoginResult(
            user=user,
            access_token=create_access_token(user.id),
            refresh_token=refresh_token,
        )


async def refresh(refresh_token: str) -> TokenPair:
    """Rotate a refresh token: the presented one is marked used and a new
    one, in the same family and with the same expiry, is returned with a new
    access token. One transaction, under the family's lock.

    A used token presented again is the grace case (see _grace_resend) or
    reuse; a revoked token is refused. Reuse revokes the whole family and
    commits that before the 401 is raised: the raise rolls the transaction
    back, so a revocation not yet committed would be lost."""
    token_id = _refresh_token_id(refresh_token)
    presented_hash = hash_refresh_token(refresh_token)

    async with UnitOfWork() as uow:
        family_id = await uow.refresh_tokens.family_of(token_id)
        if family_id is None:
            raise InvalidTokenError()
        await uow.refresh_tokens.lock_family(family_id)
        presented = await uow.refresh_tokens.get_for_update(token_id)
        if presented is None or not hmac.compare_digest(presented.token_hash, presented_hash):
            raise InvalidTokenError()

        user = await uow.users.get(presented.user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError()

        if presented.revoked_at is not None:
            raise InvalidTokenError()

        if presented.used_at is None:
            new_refresh_token = await _rotate(uow, presented)
        else:
            resent = await _grace_resend(uow, presented)
            if resent is None:
                await _revoke_for_reuse(uow, presented)
                raise InvalidTokenError()
            new_refresh_token = resent

        await uow.commit()
        return TokenPair(access_token=create_access_token(user.id), refresh_token=new_refresh_token)


async def logout(refresh_token: str) -> None:
    """Revoke the token's whole family. Silent whatever the token is
    (unknown, garbage, expired, already revoked), so the route can answer
    204 every time and tell a caller nothing.

    Access tokens are not revoked: one issued before the logout stays valid
    until its own exp, up to JWT_ACCESS_MINUTES (KNOWN_LIMITATIONS, the
    session entry)."""
    try:
        token_id = _refresh_token_id(refresh_token)
    except InvalidTokenError:
        return

    async with UnitOfWork() as uow:
        family_id = await uow.refresh_tokens.family_of(token_id)
        if family_id is None:
            return
        await uow.refresh_tokens.lock_family(family_id)
        presented = await uow.refresh_tokens.get_for_update(token_id)
        if presented is None or not hmac.compare_digest(
            presented.token_hash, hash_refresh_token(refresh_token)
        ):
            return
        revoked = await uow.refresh_tokens.revoke_family(family_id, "logout")
        await uow.commit()
    logger.info(
        "logout revoked refresh-token family %s of user %s (%d token(s))",
        family_id,
        presented.user_id,
        revoked,
    )


async def list_memberships(user_id: uuid.UUID) -> list[OrganizationMembership]:
    async with UnitOfWork() as uow:
        return await uow.memberships.list_for_user(user_id)


def _refresh_token_id(refresh_token: str) -> uuid.UUID:
    """Verify the JWT (signature, exp, type) and return its jti. A refresh
    token issued before migration 021 has no jti and is refused: every user
    signed in before then logs in once."""
    try:
        payload = decode_token(refresh_token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError() from exc
    if payload.get("token_type") != "refresh":
        raise InvalidTokenError()
    jti = payload.get("jti")
    if not isinstance(jti, str):
        raise InvalidTokenError()
    try:
        return uuid.UUID(jti)
    except ValueError as exc:
        raise InvalidTokenError() from exc


async def _issue_refresh_token(
    uow: UnitOfWork, user_id: uuid.UUID, family_id: uuid.UUID, expires_at: datetime
) -> tuple[RefreshToken, str]:
    token_id = uuid.uuid4()
    token = create_refresh_token(user_id, token_id, expires_at)
    row = await uow.refresh_tokens.create(
        token_id=token_id,
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_refresh_token(token),
        expires_at=expires_at,
    )
    return row, token


async def _rotate(uow: UnitOfWork, presented: RefreshToken) -> str:
    # Marked used before the successor is inserted: the other order would
    # put two live tokens in the family for one statement, and the partial
    # unique index refuses that.
    await uow.refresh_tokens.mark_used(presented.id)
    successor, token = await _issue_refresh_token(
        uow, presented.user_id, presented.family_id, presented.expires_at
    )
    await uow.refresh_tokens.set_replaced_by(presented.id, successor.id)
    logger.info(
        "rotated refresh token %s to %s in family %s",
        presented.id,
        successor.id,
        presented.family_id,
    )
    return token


async def _grace_resend(uow: UnitOfWork, presented: RefreshToken) -> str | None:
    """The grace rule for a used token. Within REFRESH_REUSE_GRACE of its
    use, and only while its successor has never been used or revoked, the
    successor is sent again: minted from its row, which gives the same
    string the rotation returned (create_refresh_token has no `iat`).
    Nothing is written. Every request that carried the presented token
    therefore gets the same cookie back, so a browser whose tabs refreshed
    at once ends on that live successor whichever answer it keeps (plan
    item 34, option 2's condition). Returns the token, or None when the rule
    does not apply, which the caller treats as reuse."""
    if presented.replaced_by_id is None:
        return None
    if not await uow.refresh_tokens.used_within(presented.id, REFRESH_REUSE_GRACE):
        return None
    successor = await uow.refresh_tokens.get_for_update(presented.replaced_by_id)
    if successor is None or successor.used_at is not None or successor.revoked_at is not None:
        return None
    token = create_refresh_token(successor.user_id, successor.id, successor.expires_at)
    if not hmac.compare_digest(hash_refresh_token(token), successor.token_hash):
        # The minting changed since the successor was issued: a server
        # fault, not theft, so the family is not revoked. The client gets
        # 401 and signs in again.
        logger.error(
            "grace re-send of refresh token %s did not reproduce its stored hash; family %s",
            successor.id,
            presented.family_id,
        )
        raise InvalidTokenError()
    logger.info(
        "re-sent refresh token %s within the grace window for %s in family %s",
        successor.id,
        presented.id,
        presented.family_id,
    )
    return token


async def _revoke_for_reuse(uow: UnitOfWork, presented: RefreshToken) -> None:
    revoked = await uow.refresh_tokens.revoke_family(presented.family_id, "reuse")
    await uow.commit()
    logger.warning(
        "refresh token reuse: token %s presented again; family %s of user %s revoked (%d token(s))",
        presented.id,
        presented.family_id,
        presented.user_id,
        revoked,
    )
