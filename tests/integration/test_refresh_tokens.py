"""Plan item 34, option 3: server-side refresh-token rotation, reuse
detection, the grace re-send (option 2), logout, legacy tokens and cleanup,
against real Postgres. Every row state is read back in a fresh UnitOfWork, so a
check sees only what was committed."""

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from tenderos_backend.core.security import (
    _create_token,
    create_refresh_token,
    decode_token,
    hash_refresh_token,
    refresh_family_expiry,
)
from tenderos_backend.db.models.refresh_token import RefreshToken
from tenderos_backend.db.models.user import User
from tenderos_backend.db.repositories.refresh_tokens import RefreshTokenRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import auth_service

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 10
# Random per run: these tests never need to know it, only to reuse it.
_TEST_PASSWORD = uuid.uuid4().hex


async def _register() -> auth_service.RegisterResult:
    return await auth_service.register(
        email=f"{uuid.uuid4()}@example.com",
        password=_TEST_PASSWORD,
        organization_name="Refresh Rotation Org",
    )


def _jti(token: str) -> uuid.UUID:
    return uuid.UUID(str(decode_token(token)["jti"]))


async def _row(token_id: uuid.UUID) -> RefreshToken:
    async with UnitOfWork() as uow:
        row = await uow.session.get(RefreshToken, token_id)
    assert row is not None
    return row


async def _family(family_id: uuid.UUID) -> list[RefreshToken]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(RefreshToken)
            .where(RefreshToken.family_id == family_id)
            .order_by(RefreshToken.issued_at, RefreshToken.id)
        )
        return list(result.scalars().all())


async def _live_count(family_id: uuid.UUID) -> int:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(func.count())
            .select_from(RefreshToken)
            .where(
                RefreshToken.family_id == family_id,
                RefreshToken.used_at.is_(None),
                RefreshToken.revoked_at.is_(None),
                RefreshToken.expires_at > func.now(),
            )
        )
        return result.scalar_one()


async def _age_use(token_id: uuid.UUID, seconds: float) -> None:
    """Move a token's used_at into the past, on the database clock, so a
    test can step outside the grace window without sleeping."""
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(used_at=func.now() - timedelta(seconds=seconds))
        )
        await uow.commit()


def _past_grace() -> float:
    return auth_service.REFRESH_REUSE_GRACE.total_seconds() + 1


# --- rotation ---------------------------------------------------------------


async def test_a_refresh_rotates_the_token_and_the_new_one_works_once():
    registered = await _register()
    first = registered.refresh_token

    pair = await auth_service.refresh(first)

    assert pair.refresh_token != first
    assert decode_token(pair.access_token)["token_type"] == "access"
    old = await _row(_jti(first))
    new = await _row(_jti(pair.refresh_token))
    assert old.used_at is not None
    assert old.revoked_at is None
    assert old.replaced_by_id == new.id
    assert new.family_id == old.family_id
    assert new.used_at is None and new.revoked_at is None
    assert new.token_hash == hash_refresh_token(pair.refresh_token)
    assert await _live_count(old.family_id) == 1

    third = await auth_service.refresh(pair.refresh_token)
    assert (await _row(_jti(pair.refresh_token))).replaced_by_id == _jti(third.refresh_token)


async def test_a_rotated_token_keeps_the_family_expiry():
    """Rotation never extends a session: every member carries the login's
    expires_at, and the JWT's exp says the same."""
    registered = await _register()
    pair = await auth_service.refresh(registered.refresh_token)
    pair = await auth_service.refresh(pair.refresh_token)

    first = await _row(_jti(registered.refresh_token))
    members = await _family(first.family_id)
    assert len(members) == 3
    assert {member.expires_at for member in members} == {first.expires_at}
    assert decode_token(pair.refresh_token)["exp"] == decode_token(registered.refresh_token)["exp"]


async def test_login_starts_a_new_family_each_time():
    registered = await _register()
    login = await auth_service.login(email=registered.user.email, password=_TEST_PASSWORD)

    first = await _row(_jti(registered.refresh_token))
    second = await _row(_jti(login.refresh_token))
    assert first.family_id != second.family_id
    assert await _live_count(first.family_id) == 1
    assert await _live_count(second.family_id) == 1


# --- reuse ------------------------------------------------------------------


async def test_reuse_after_the_grace_window_revokes_the_whole_family_and_commits():
    registered = await _register()
    first = registered.refresh_token
    pair = await auth_service.refresh(first)
    await _age_use(_jti(first), _past_grace())

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(first)

    # Read in a fresh transaction: the revocation was committed although
    # the call raised.
    members = await _family((await _row(_jti(first))).family_id)
    assert [m.revoked_reason for m in members] == ["reuse", "reuse"]
    assert all(m.revoked_at is not None for m in members)
    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(pair.refresh_token)


async def test_a_used_token_whose_successor_was_used_is_reuse_even_within_the_window():
    registered = await _register()
    first = registered.refresh_token
    second = await auth_service.refresh(first)
    await auth_service.refresh(second.refresh_token)

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(first)

    members = await _family((await _row(_jti(first))).family_id)
    assert {m.revoked_reason for m in members} == {"reuse"}
    assert await _live_count(members[0].family_id) == 0


# --- the grace re-send (option 2) --------------------------------------------


async def test_a_refresh_token_mints_the_same_string_from_its_row():
    """What the re-send rests on: no claim but the row's, so minting again
    from the row gives the token byte for byte."""
    registered = await _register()
    row = await _row(_jti(registered.refresh_token))

    again = create_refresh_token(row.user_id, row.id, row.expires_at)

    assert again == registered.refresh_token
    assert "iat" not in decode_token(again)


async def test_within_the_window_the_unused_successor_is_sent_again():
    registered = await _register()
    first = registered.refresh_token
    lost = await auth_service.refresh(first)

    retried = await auth_service.refresh(first)

    assert retried.refresh_token == lost.refresh_token
    assert retried.access_token
    successor = await _row(_jti(lost.refresh_token))
    assert successor.used_at is None and successor.revoked_at is None
    assert (await _row(_jti(first))).replaced_by_id == successor.id
    members = await _family(successor.family_id)
    assert len(members) == 2
    assert {m.revoked_reason for m in members} == {None}
    assert await _live_count(successor.family_id) == 1
    # The successor is a normal live token.
    await auth_service.refresh(retried.refresh_token)


async def test_a_resend_that_does_not_reproduce_the_stored_hash_is_refused_without_revoking():
    """A mismatch means the minting changed, a server fault: 401, but the
    family is not revoked as if the token had been stolen."""
    registered = await _register()
    first = registered.refresh_token
    lost = await auth_service.refresh(first)
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == _jti(lost.refresh_token))
            .values(token_hash="e" * 64)
        )
        await uow.commit()

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(first)

    members = await _family((await _row(_jti(first))).family_id)
    assert {m.revoked_reason for m in members} == {None}


async def test_a_token_superseded_before_option_2_is_refused():
    """'superseded' has no writer any more; a row written before is revoked,
    and refused like any revoked token."""
    registered = await _register()
    token_id = _jti(registered.refresh_token)
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == token_id)
            .values(revoked_at=func.now(), revoked_reason="superseded")
        )
        await uow.commit()

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(registered.refresh_token)


# --- logout -----------------------------------------------------------------


async def test_logout_revokes_the_family_and_the_token_then_fails():
    registered = await _register()
    pair = await auth_service.refresh(registered.refresh_token)

    await auth_service.logout(pair.refresh_token)

    members = await _family((await _row(_jti(pair.refresh_token))).family_id)
    assert [m.revoked_reason for m in members] == ["logout", "logout"]
    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(pair.refresh_token)


async def test_logout_with_an_older_used_token_still_revokes_the_family():
    registered = await _register()
    pair = await auth_service.refresh(registered.refresh_token)

    await auth_service.logout(registered.refresh_token)

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(pair.refresh_token)


async def test_logout_is_silent_for_garbage_unknown_legacy_and_access_tokens():
    registered = await _register()
    # Validly signed, with a jti that has no row.
    unknown = create_refresh_token(registered.user.id, uuid.uuid4(), refresh_family_expiry())
    legacy = _create_token(registered.user.id, "refresh", timedelta(days=1))

    for token in ("not a jwt", registered.access_token, unknown, legacy):
        await auth_service.logout(token)

    # Nothing was revoked by any of them.
    family_id = (await _row(_jti(registered.refresh_token))).family_id
    assert await _live_count(family_id) == 1


async def test_logout_does_not_end_an_access_token_already_issued():
    """The accepted limit, asserted so it stays visible: logout revokes the
    refresh family only. An access token issued before it still verifies
    until its own exp (up to JWT_ACCESS_MINUTES)."""
    registered = await _register()

    await auth_service.logout(registered.refresh_token)

    payload = decode_token(registered.access_token)
    assert payload["user_id"] == str(registered.user.id)


# --- legacy tokens ----------------------------------------------------------


async def test_a_refresh_token_from_before_migration_021_is_refused():
    """Signed and unexpired, but with no jti: the shape every refresh token
    had before this change (user_id, token_type, iat, exp)."""
    registered = await _register()
    legacy = _create_token(registered.user.id, "refresh", timedelta(days=7))
    assert "jti" not in decode_token(legacy)

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(legacy)


async def test_a_token_whose_hash_does_not_match_its_row_is_refused():
    registered = await _register()
    token_id = _jti(registered.refresh_token)
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(RefreshToken).where(RefreshToken.id == token_id).values(token_hash="0" * 64)
        )
        await uow.commit()

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(registered.refresh_token)


async def test_an_inactive_user_cannot_refresh():
    registered = await _register()
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(User).where(User.id == registered.user.id).values(is_active=False)
        )
        await uow.commit()

    with pytest.raises(auth_service.InvalidTokenError):
        await auth_service.refresh(registered.refresh_token)


# --- concurrency and the invariant ------------------------------------------


def _hold_at_the_family_lock(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every refresh waits at a barrier just before it takes the family
    lock, so all `parties` calls have read the presented token's family and
    are about to lock it at the same moment: they genuinely overlap, on
    separate connections, instead of running one after another by luck."""
    barrier = asyncio.Barrier(parties)
    original = RefreshTokenRepository.lock_family

    async def lock_together(self, family_id):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        return await original(self, family_id)

    monkeypatch.setattr(RefreshTokenRepository, "lock_family", lock_together)


async def test_two_refreshes_racing_with_one_token(monkeypatch):
    """Two tabs (or a client retrying a lost response) send the same token
    at once. Exactly one rotates; the other takes the grace path and gets the
    same successor back (plan item 34, option 2's condition): a browser
    keeps whichever Set-Cookie it processes last, and both are the one live
    token. Never two live successors, never a revoked family. Repeated, since
    one pass could pass by luck."""
    for _ in range(5):
        registered = await _register()
        _hold_at_the_family_lock(monkeypatch, 2)
        token = registered.refresh_token

        pairs = await asyncio.wait_for(
            asyncio.gather(auth_service.refresh(token), auth_service.refresh(token)),
            timeout=_WAIT_SECONDS,
        )
        monkeypatch.undo()

        family_id = (await _row(_jti(token))).family_id
        members = await _family(family_id)
        assert pairs[0].refresh_token == pairs[1].refresh_token
        assert len(members) == 2
        assert {m.revoked_reason for m in members} == {None}
        assert await _live_count(family_id) == 1
        # The token both got rotates normally.
        await auth_service.refresh(pairs[0].refresh_token)


async def test_the_invariant_holds_under_concurrent_refreshes_and_grace_resends(monkeypatch):
    """Six refreshes of one token, gathered: one rotation and five grace
    re-sends of the same successor. Then the family's one live token and the
    first token, raced. Repeated. After every round, at most one live token
    in the family."""
    for _ in range(3):
        registered = await _register()
        first = registered.refresh_token
        family_id = (await _row(_jti(first))).family_id

        _hold_at_the_family_lock(monkeypatch, 6)
        pairs = await asyncio.wait_for(
            asyncio.gather(*(auth_service.refresh(first) for _ in range(6))),
            timeout=_WAIT_SECONDS,
        )
        monkeypatch.undo()
        assert await _live_count(family_id) == 1
        members = await _family(family_id)
        assert len(members) == 2
        assert {m.revoked_reason for m in members} == {None}
        assert len({pair.refresh_token for pair in pairs}) == 1
        live = pairs[0].refresh_token

        # The two orders differ, and both keep the invariant. The live token
        # first: it rotates, so the first token's successor is used and the
        # first token is reuse (one success, one 401, nothing live). The
        # first token first: a grace re-send of the live token, which then
        # rotates (two successes, one live token).
        _hold_at_the_family_lock(monkeypatch, 2)
        results = await asyncio.wait_for(
            asyncio.gather(
                auth_service.refresh(live), auth_service.refresh(first), return_exceptions=True
            ),
            timeout=_WAIT_SECONDS,
        )
        monkeypatch.undo()
        outcome = sorted(type(r).__name__ for r in results)
        reasons = [m.revoked_reason for m in await _family(family_id)]
        if outcome == ["InvalidTokenError", "TokenPair"]:
            assert await _live_count(family_id) == 0
            assert "reuse" in reasons
        else:
            assert outcome == ["TokenPair", "TokenPair"], results
            assert await _live_count(family_id) == 1
            assert "reuse" not in reasons


async def test_two_spent_tokens_of_one_family_presented_at_once_do_not_deadlock(monkeypatch):
    """The case the family lock exists for. Two different used tokens of one
    family, both past the grace window (a thief replaying two old tokens,
    or a thief and the user's own stale tab), arrive together. Each is
    reuse and revokes the whole family. With only a row lock on each
    presented token, each request would hold its own row and then wait for
    the other's inside revoke_family: a deadlock, which Postgres ends by
    failing one request with an error. Under the family lock they run one
    after the other: both 401, the family revoked once."""
    for _ in range(3):
        registered = await _register()
        first = registered.refresh_token
        second = (await auth_service.refresh(first)).refresh_token
        await auth_service.refresh(second)
        await _age_use(_jti(first), _past_grace())
        await _age_use(_jti(second), _past_grace())

        _hold_at_the_family_lock(monkeypatch, 2)
        results = await asyncio.wait_for(
            asyncio.gather(
                auth_service.refresh(first), auth_service.refresh(second), return_exceptions=True
            ),
            timeout=_WAIT_SECONDS,
        )
        monkeypatch.undo()

        assert [type(r).__name__ for r in results] == ["InvalidTokenError"] * 2, results
        members = await _family((await _row(_jti(first))).family_id)
        assert {m.revoked_reason for m in members} == {"reuse"}


async def test_the_database_refuses_a_second_live_token_in_a_family():
    registered = await _register()
    existing = await _row(_jti(registered.refresh_token))

    with pytest.raises(IntegrityError, match="uq_refresh_token_live_per_family"):
        async with UnitOfWork() as uow:
            await uow.refresh_tokens.create(
                token_id=uuid.uuid4(),
                user_id=existing.user_id,
                family_id=existing.family_id,
                token_hash="f" * 64,
                expires_at=existing.expires_at,
            )
            await uow.commit()


# --- cleanup ----------------------------------------------------------------


async def _seed(
    user_id: uuid.UUID, family_id: uuid.UUID, expires_in: timedelta, used: bool
) -> uuid.UUID:
    token_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        await uow.session.execute(
            text(
                "INSERT INTO refresh_tokens (id, user_id, family_id, token_hash, expires_at, "
                "used_at) VALUES (:id, :user_id, :family_id, :hash, now() + :expires_in, "
                "CASE WHEN :used THEN now() END)"
            ),
            {
                "id": token_id,
                "user_id": user_id,
                "family_id": family_id,
                "hash": uuid.uuid4().hex,
                "expires_in": expires_in,
                "used": used,
            },
        )
        await uow.commit()
    return token_id


async def test_delete_expired_removes_only_expired_rows_in_bounded_batches():
    registered = await _register()
    user_id = registered.user.id
    # An expired chain of three (each used, linked to the next), so a batch
    # can delete a successor before its predecessor (SET NULL), plus two
    # more expired families and the live registration family.
    chain_family = uuid.uuid4()
    chain = [await _seed(user_id, chain_family, timedelta(days=-1), used=i < 2) for i in range(3)]
    async with UnitOfWork() as uow:
        for earlier, later in zip(chain, chain[1:], strict=False):
            await uow.refresh_tokens.set_replaced_by(earlier, later)
        await uow.commit()
    for _ in range(2):
        await _seed(user_id, uuid.uuid4(), timedelta(seconds=-1), used=False)

    deleted_per_call = []
    for _ in range(4):
        async with UnitOfWork() as uow:
            deleted_per_call.append(await uow.refresh_tokens.delete_expired(batch_size=2))
            await uow.commit()

    assert deleted_per_call == [2, 2, 1, 0]
    async with UnitOfWork() as uow:
        remaining = (await uow.session.execute(select(RefreshToken.id))).scalars().all()
    assert remaining == [_jti(registered.refresh_token)]


async def test_delete_expired_rejects_a_non_positive_batch_size():
    async with UnitOfWork() as uow:
        with pytest.raises(ValueError):
            await uow.refresh_tokens.delete_expired(batch_size=0)
