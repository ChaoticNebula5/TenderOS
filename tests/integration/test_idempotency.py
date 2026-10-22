import asyncio
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from tenderos_api.errors import APIError
from tenderos_api.idempotency import IdempotencyContext, request_hash
from tenderos_api.main import app
from tenderos_backend.db.models.idempotency import IdempotencyRecord
from tenderos_backend.db.repositories.idempotency import IdempotencyRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import tender_service

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register_admin(client: AsyncClient, email: str, org_name: str) -> tuple[str, str]:
    resp = await client.post(
        "/auth/register",
        json={"email": email, "password": "hunter2", "organization_name": org_name},
    )
    data = resp.json()
    return data["access_token"], data["organization_id"]


async def test_duplicate_idempotency_key_same_body_replays_response():
    """Depends on both dependencies.require_permission and idempotency.
    get_idempotency_context being implemented - errors with 500 until both
    TODOs are filled in."""
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-a@example.com", "Idem Org A")
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-tender-1"}
        body = {"name": "Idempotent RFP"}

        first = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)
        second = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json() == second.json()

        listing = await client.get(
            f"/organizations/{org_id}/tenders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(listing.json()) == 1


# Upper bound on every wait in the two overlap tests below: a regression
# fails the test within this time instead of hanging the suite.
_WAIT_SECONDS = 10


def _pause_tender_creation(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """Patch create_tender so the request that reaches it sets `entered`, then
    waits for `release` before doing the real work. By then that request's
    in_progress idempotency record is already committed (get_idempotency_
    context runs as a route dependency, before the route body), so a second
    request sent while it waits genuinely overlaps it. The route calls
    tender_service.create_tender through the module attribute with keyword
    arguments only (routes/organizations.py), which is what this patches."""
    entered = asyncio.Event()
    release = asyncio.Event()
    original = tender_service.create_tender

    async def paused(**kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), timeout=_WAIT_SECONDS)
        return await original(**kwargs)

    monkeypatch.setattr(tender_service, "create_tender", paused)
    return entered, release


async def _send_second_while_first_is_paused(
    client: AsyncClient,
    url: str,
    body: dict,
    headers: dict,
    entered: asyncio.Event,
    release: asyncio.Event,
    before_second: Callable[[], None] | None = None,
):
    """Start request A, wait until it is paused inside create_tender, run
    `before_second` if given, send B, then release A. Returns (A's response,
    B's response). release is set in `finally`, so A is never left waiting
    even if B errors or times out."""
    first_task = asyncio.create_task(client.post(url, json=body, headers=headers))
    try:
        await asyncio.wait_for(entered.wait(), timeout=_WAIT_SECONDS)
        if before_second is not None:
            before_second()
        second = await asyncio.wait_for(
            client.post(url, json=body, headers=headers), timeout=_WAIT_SECONDS
        )
    finally:
        release.set()
        first = await asyncio.wait_for(first_task, timeout=_WAIT_SECONDS)
    return first, second


async def _assert_one_tender_with_id(client: AsyncClient, org_id: str, token: str, tender_id: str):
    listing = await client.get(
        f"/organizations/{org_id}/tenders", headers={"Authorization": f"Bearer {token}"}
    )
    tenders = listing.json()
    assert len(tenders) == 1, f"expected exactly one tender, got {len(tenders)}"
    assert tenders[0]["id"] == tender_id


async def test_same_key_while_first_request_in_progress_is_in_progress(
    monkeypatch: pytest.MonkeyPatch,
):
    """B16 guarantee, forced rather than hoped for: while request A holds an
    in_progress record for an Idempotency-Key, request B with the same key
    and body gets a clean 409 - never a second tender and never a 500. A is
    then released and finishes as 201, and exactly one tender exists.

    Phase 4 blocker 2 changed the code B gets from IDEMPOTENCY_CONFLICT to
    IDEMPOTENCY_IN_PROGRESS, with Retry-After: A holds a live lease, so B
    should wait and retry the same key, not treat the key as burned.

    This replaces an asyncio.gather version that only hoped the two requests
    would overlap. In CI they did not: B reached its lookup after A had
    finished, replayed A's completed record, and the test saw [201, 201]
    while this 409 branch was never reached. Pausing A inside create_tender
    makes the overlap certain."""
    entered, release = _pause_tender_creation(monkeypatch)
    async with await _client() as client:
        token, org_id = await _register_admin(
            client, "idem-overlap@example.com", "Idem Overlap Org"
        )
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "overlap-create-tender"}
        url = f"/organizations/{org_id}/tenders"
        body = {"name": "Overlapping Idempotent RFP"}

        first, second = await _send_second_while_first_is_paused(
            client, url, body, headers, entered, release
        )

        assert second.status_code == 409, f"expected 409, got {second.status_code}: {second.text}"
        assert second.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
        assert int(second.headers["retry-after"]) > 0
        assert first.status_code == 201, f"expected 201, got {first.status_code}: {first.text}"
        await _assert_one_tender_with_id(client, org_id, token, first.json()["id"])


async def test_lost_insert_race_rereads_and_is_in_progress(monkeypatch: pytest.MonkeyPatch):
    """The other way two same-key requests can meet: both see "no record"
    and both try to insert one. B's first lookup is made to miss A's
    committed in_progress row, so B attempts its own insert, hits
    uq_idempotency_key, and must take the IntegrityError branch
    (idempotency.py: rollback, re-read, then the shared existing-record
    logic) to a 409 - not a 500 and not a second tender. If B's insert
    somehow succeeded instead, B would reach the paused create_tender and
    time out, failing the test. Since Phase 4 blocker 2 that 409 is
    IDEMPOTENCY_IN_PROGRESS: the winner A holds a live lease."""
    entered, release = _pause_tender_creation(monkeypatch)
    original_get = IdempotencyRepository.get
    hide_next_lookup = {"armed": False}

    async def get_missing_once(self, organization_id, operation, idempotency_key):
        if hide_next_lookup["armed"]:
            hide_next_lookup["armed"] = False
            return None
        return await original_get(self, organization_id, operation, idempotency_key)

    monkeypatch.setattr(IdempotencyRepository, "get", get_missing_once)

    async with await _client() as client:
        token, org_id = await _register_admin(
            client, "idem-lost-race@example.com", "Idem Lost Race Org"
        )
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "lost-race-create-tender"}
        url = f"/organizations/{org_id}/tenders"
        body = {"name": "Lost Race Idempotent RFP"}

        def arm() -> None:
            # Only B's lookup may miss: A's ran (for real) before it paused.
            hide_next_lookup["armed"] = True

        first, second = await _send_second_while_first_is_paused(
            client, url, body, headers, entered, release, before_second=arm
        )

        assert hide_next_lookup["armed"] is False, "B's first lookup was never made"
        assert second.status_code == 409, f"expected 409, got {second.status_code}: {second.text}"
        assert second.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
        assert first.status_code == 201, f"expected 201, got {first.status_code}: {first.text}"
        await _assert_one_tender_with_id(client, org_id, token, first.json()["id"])


async def test_duplicate_idempotency_key_different_body_conflicts():
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-b@example.com", "Idem Org B")
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "create-tender-2"}

        first = await client.post(
            f"/organizations/{org_id}/tenders", json={"name": "First Name"}, headers=headers
        )
        second = await client.post(
            f"/organizations/{org_id}/tenders", json={"name": "Different Name"}, headers=headers
        )

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_duplicate_idempotency_key_across_two_tenders_conflicts_not_replays():
    """Regression test for the audit's finding #3: get_idempotency_context's
    record lookup key is (organization_id, operation, idempotency_key), which
    doesn't include the resource identity carried in the URL path (tender_id
    here) - only request_hash could have caught this, and it used to be
    sha256(body_bytes) alone. Two different tenders in the SAME organization,
    hit with the same Idempotency-Key and an identical (empty) body, used to
    collide on one idempotency record: the second request silently replayed
    tender A's cached response instead of ever running project_tender for
    tender B - confirmed live before the fix (tender B's own graph/project
    response came back with tender A's id in it).

    Fixed by folding request.method + request.url.path into request_hash, so
    two different resources now hit the pre-existing request_hash-mismatch
    branch and 409 as IDEMPOTENCY_CONFLICT - the same real conflict a
    different body already produced, just recognizing more of what "the same
    request" means. See the sibling test below for the same-tender case,
    which must still cleanly replay.
    """
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-graph@example.com", "Idem Graph Org")
        create_headers = {"Authorization": f"Bearer {token}"}

        tender_a = (
            await client.post(
                f"/organizations/{org_id}/tenders",
                json={"name": "Tender A"},
                headers=create_headers,
            )
        ).json()["id"]
        tender_b = (
            await client.post(
                f"/organizations/{org_id}/tenders",
                json={"name": "Tender B"},
                headers=create_headers,
            )
        ).json()["id"]

        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "shared-projection-key",
        }

        first = await client.post(f"/tenders/{tender_a}/graph/project", headers=headers)
        second = await client.post(f"/tenders/{tender_b}/graph/project", headers=headers)

        assert first.status_code == 200
        assert first.json()["tender_id"] == tender_a

        assert second.status_code == 409, (
            f"expected IDEMPOTENCY_CONFLICT for a reused key against a "
            f"different tender, got {second.status_code}: {second.json()}"
        )
        assert second.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_duplicate_idempotency_key_same_tender_still_replays():
    """The other half of the same fix: folding method+path into request_hash
    must not turn a genuine retry (same tender, same key, same body) into a
    false conflict - only a DIFFERENT resource should 409.
    """
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-graph-b@example.com", "Idem Graph Org B")
        create_headers = {"Authorization": f"Bearer {token}"}

        tender_id = (
            await client.post(
                f"/organizations/{org_id}/tenders",
                json={"name": "Tender Only"},
                headers=create_headers,
            )
        ).json()["id"]

        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "same-tender-key",
        }

        first = await client.post(f"/tenders/{tender_id}/graph/project", headers=headers)
        second = await client.post(f"/tenders/{tender_id}/graph/project", headers=headers)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()


async def test_missing_idempotency_key_creates_separate_tenders():
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-c@example.com", "Idem Org C")
        headers = {"Authorization": f"Bearer {token}"}
        body = {"name": "No Key RFP"}

        first = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)
        second = await client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] != second.json()["id"]


# ---------------------------------------------------------------------------
# Phase 4 blocker 2: leases, takeover, release on failure, claim guards and
# cleanup. Every record seeded below uses the same request_hash the
# dependency computes (tenderos_api.idempotency.request_hash) over the exact
# bytes the test sends, so a seeded record and a real request match.
# ---------------------------------------------------------------------------

_OPERATION = "create_tender"
_LEASE = timedelta(seconds=60)
_RETENTION = timedelta(hours=24)


def _tender_body(name: str) -> bytes:
    return json.dumps({"name": name}).encode()


def _tender_headers(token: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": key,
        "Content-Type": "application/json",
    }


async def _seed_in_progress(
    org_id: str, key: str, body: bytes, *, lease_seconds: float | None
) -> IdempotencyRecord:
    """An in_progress create_tender record for (org, key, body), as if a
    request had claimed it and then died. lease_seconds sets locked_until to
    now() + that many seconds (negative: already lapsed); None sets both
    locked_until and claim_token to NULL, the shape of a row created before
    migration 019."""
    fingerprint = request_hash("POST", f"/organizations/{org_id}/tenders", "", body)
    async with UnitOfWork() as uow:
        record = await uow.idempotency.create_in_progress(
            organization_id=UUID(org_id),
            operation=_OPERATION,
            idempotency_key=key,
            request_hash=fingerprint,
            lease=_LEASE,
            retention=_RETENTION,
        )
        if lease_seconds is None:
            await uow.session.execute(
                text(
                    "UPDATE idempotency_records SET locked_until = NULL, claim_token = NULL "
                    "WHERE id = :id"
                ),
                {"id": record.id},
            )
        else:
            await uow.session.execute(
                text(
                    "UPDATE idempotency_records "
                    "SET locked_until = now() + make_interval(secs => :secs) WHERE id = :id"
                ),
                {"id": record.id, "secs": lease_seconds},
            )
        await uow.commit()
    return record


async def _record(org_id: str, key: str) -> IdempotencyRecord | None:
    async with UnitOfWork() as uow:
        return await uow.idempotency.get(UUID(org_id), _OPERATION, key)


async def _tender_count(client: AsyncClient, org_id: str, token: str) -> int:
    listing = await client.get(
        f"/organizations/{org_id}/tenders", headers={"Authorization": f"Bearer {token}"}
    )
    return len(listing.json())


def _fail_tender_creation_once(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    """create_tender raises `error` on its first call and works after that."""
    original = tender_service.create_tender
    calls = {"n": 0}

    async def failing_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise error
        return await original(**kwargs)

    monkeypatch.setattr(tender_service, "create_tender", failing_once)


async def test_new_record_lease_and_expiry_come_from_the_db_clock():
    """locked_until and expires_at are now() + a duration inside the INSERT,
    and created_at's server default is now() in the same transaction, so the
    differences are exact. A value computed from the API host's clock would
    not be."""
    async with await _client() as client:
        _, org_id = await _register_admin(client, "idem-clock@example.com", "Idem Clock Org")
    fingerprint = request_hash("POST", f"/organizations/{org_id}/tenders", "", b"{}")
    async with UnitOfWork() as uow:
        record = await uow.idempotency.create_in_progress(
            organization_id=UUID(org_id),
            operation=_OPERATION,
            idempotency_key="clock-key",
            request_hash=fingerprint,
            lease=_LEASE,
            retention=_RETENTION,
        )
        await uow.commit()
    async with UnitOfWork() as uow:
        row = (
            await uow.session.execute(
                text(
                    "SELECT locked_until - created_at AS lease, expires_at - created_at AS kept "
                    "FROM idempotency_records WHERE id = :id"
                ),
                {"id": record.id},
            )
        ).one()
    assert row.lease == _LEASE
    assert row.kept == _RETENTION
    assert record.status == "in_progress"
    assert record.claim_token is not None


async def test_handler_apierror_releases_the_key(monkeypatch: pytest.MonkeyPatch):
    """An APIError from the route used to leave the record in_progress, so a
    retry with the same key got 409 for good. Now the record is deleted on
    the way out and the retry runs the work."""
    _fail_tender_creation_once(monkeypatch, APIError(409, "TEST_REFUSAL", "Refused once."))
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-apierr@example.com", "Idem ApiErr Org")
        url = f"/organizations/{org_id}/tenders"
        body = _tender_body("Refused Then Created")
        headers = _tender_headers(token, "apierror-key")

        first = await client.post(url, content=body, headers=headers)
        assert first.status_code == 409, first.text
        assert first.json()["error"]["code"] == "TEST_REFUSAL"
        assert await _record(org_id, "apierror-key") is None

        second = await client.post(url, content=body, headers=headers)
        assert second.status_code == 201, second.text
        assert await _tender_count(client, org_id, token) == 1


async def test_retry_after_an_unhandled_500_with_the_same_key_succeeds(
    monkeypatch: pytest.MonkeyPatch,
):
    """The GateDecisionPanel case: attempt 1 hits a transient 500 and the
    client retries with the same key. That retry used to get 409
    IDEMPOTENCY_CONFLICT; now the failed attempt released the key."""
    _fail_tender_creation_once(monkeypatch, RuntimeError("transient failure"))
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-500@example.com", "Idem 500 Org")
        url = f"/organizations/{org_id}/tenders"
        body = _tender_body("Failed Then Created")
        headers = _tender_headers(token, "five-hundred-key")

        first = await client.post(url, content=body, headers=headers)
        assert first.status_code == 500, first.text
        assert first.json()["error"]["code"] == "INTERNAL_ERROR"

        second = await client.post(url, content=body, headers=headers)
        assert second.status_code == 201, second.text
        assert await _tender_count(client, org_id, token) == 1


async def test_live_lease_returns_in_progress_with_retry_after():
    """A first attempt that still holds its lease: the retry is told to
    wait, and Retry-After is the time left on that lease (within a second,
    since the lease is read in SQL a moment after the response)."""
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-live@example.com", "Idem Live Org")
        body = _tender_body("Live Lease")
        record = await _seed_in_progress(org_id, "live-key", body, lease_seconds=30)

        response = await client.post(
            f"/organizations/{org_id}/tenders",
            content=body,
            headers=_tender_headers(token, "live-key"),
        )

        assert response.status_code == 409, response.text
        assert response.json() == {
            "error": {
                "code": "IDEMPOTENCY_IN_PROGRESS",
                "message": (
                    "This request is already being processed. Please wait a moment and try again."
                ),
            }
        }
        async with UnitOfWork() as uow:
            remaining = (
                await uow.session.execute(
                    text(
                        "SELECT extract(epoch FROM locked_until - now()) "
                        "FROM idempotency_records WHERE id = :id"
                    ),
                    {"id": record.id},
                )
            ).scalar_one()
        assert abs(int(response.headers["retry-after"]) - float(remaining)) <= 1
        assert await _tender_count(client, org_id, token) == 0


async def test_different_body_on_an_in_progress_key_is_still_a_conflict():
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-diff@example.com", "Idem Diff Org")
        await _seed_in_progress(org_id, "diff-key", _tender_body("Original"), lease_seconds=30)

        response = await client.post(
            f"/organizations/{org_id}/tenders",
            content=_tender_body("Something Else"),
            headers=_tender_headers(token, "diff-key"),
        )

        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


async def test_expired_lease_is_taken_over():
    """The first attempt died and its lease lapsed: the retry
    takes the record over under a new claim token and does the work."""
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-expired@example.com", "Idem Exp Org")
        body = _tender_body("Expired Lease")
        seeded = await _seed_in_progress(org_id, "expired-key", body, lease_seconds=-1)

        response = await client.post(
            f"/organizations/{org_id}/tenders",
            content=body,
            headers=_tender_headers(token, "expired-key"),
        )

        assert response.status_code == 201, response.text
        record = await _record(org_id, "expired-key")
        assert record is not None
        assert record.status == "completed"
        assert record.claim_token != seeded.claim_token
        assert await _tender_count(client, org_id, token) == 1


async def test_pre_migration_row_is_taken_over():
    """A record from before migration 019 has locked_until and
    claim_token NULL. It counts as expired, so its key is not stranded."""
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-null@example.com", "Idem Null Org")
        body = _tender_body("Pre Migration")
        await _seed_in_progress(org_id, "null-key", body, lease_seconds=None)

        response = await client.post(
            f"/organizations/{org_id}/tenders",
            content=body,
            headers=_tender_headers(token, "null-key"),
        )

        assert response.status_code == 201, response.text
        record = await _record(org_id, "null-key")
        assert record is not None
        assert record.status == "completed"
        assert record.claim_token is not None
        assert await _tender_count(client, org_id, token) == 1


async def test_concurrent_takeover_has_exactly_one_winner(monkeypatch: pytest.MonkeyPatch):
    """Two retries find the same lapsed lease. A barrier holds
    both until each is about to run take_over_expired, so the two UPDATEs
    really are concurrent; Postgres serialises them on the row lock and only
    the first matches its WHERE. The winner is paused inside create_tender
    so it still holds its new lease when the loser answers: the loser gets
    IDEMPOTENCY_IN_PROGRESS, the winner then finishes with 201, and exactly
    one tender exists."""
    entered, release = _pause_tender_creation(monkeypatch)
    barrier = asyncio.Barrier(2)
    original_take_over = IdempotencyRepository.take_over_expired

    async def take_over_together(self, record_id, lease):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        return await original_take_over(self, record_id, lease)

    monkeypatch.setattr(IdempotencyRepository, "take_over_expired", take_over_together)

    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-race@example.com", "Idem Race Org")
        url = f"/organizations/{org_id}/tenders"
        body = _tender_body("Raced Takeover")
        headers = _tender_headers(token, "race-key")
        await _seed_in_progress(org_id, "race-key", body, lease_seconds=-1)

        tasks = [
            asyncio.create_task(client.post(url, content=body, headers=headers)) for _ in range(2)
        ]
        try:
            done, _ = await asyncio.wait(
                tasks, timeout=_WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
            assert done, "neither request answered"
            loser = next(iter(done)).result()
        finally:
            release.set()
        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=_WAIT_SECONDS)

        assert loser.status_code == 409, loser.text
        assert loser.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
        assert sorted(r.status_code for r in responses) == [201, 409]
        assert entered.is_set()
        assert await _tender_count(client, org_id, token) == 1


async def test_original_cannot_release_after_a_takeover():
    """The original request is still alive after losing its lease,
    and then fails. Its release must not delete the new holder's record."""
    async with await _client() as client:
        _, org_id = await _register_admin(client, "idem-rel@example.com", "Idem Release Org")
    seeded = await _seed_in_progress(org_id, "rel-key", _tender_body("Rel"), lease_seconds=-1)
    async with UnitOfWork() as uow:
        taken = await uow.idempotency.take_over_expired(seeded.id, _LEASE)
        assert taken is not None
        new_token = taken.claim_token
        await uow.commit()

    original = IdempotencyContext(
        active=True, operation=_OPERATION, record_id=seeded.id, claim_token=seeded.claim_token
    )
    await original.release()

    record = await _record(org_id, "rel-key")
    assert record is not None, "the original's release deleted the new holder's record"
    assert record.status == "in_progress"
    assert record.claim_token == new_token


async def test_original_cannot_finalize_after_a_takeover(caplog: pytest.LogCaptureFixture):
    """The original request finishes after losing its lease. Its
    finalize must not overwrite the new holder's record, and it logs a
    WARNING instead; the new holder can still complete it."""
    caplog.set_level(logging.WARNING, logger="tenderos_api.idempotency")
    async with await _client() as client:
        _, org_id = await _register_admin(client, "idem-fin@example.com", "Idem Finalize Org")
    seeded = await _seed_in_progress(org_id, "fin-key", _tender_body("Fin"), lease_seconds=-1)
    async with UnitOfWork() as uow:
        taken = await uow.idempotency.take_over_expired(seeded.id, _LEASE)
        assert taken is not None
        new_token = taken.claim_token
        await uow.commit()

    original = IdempotencyContext(
        active=True, operation=_OPERATION, record_id=seeded.id, claim_token=seeded.claim_token
    )
    await original.finalize(201, {"id": "from-the-original"})

    record = await _record(org_id, "fin-key")
    assert record is not None
    assert record.status == "in_progress"
    assert record.response_body is None
    assert any(
        "lost before finalize" in r.getMessage() and r.levelno == logging.WARNING
        for r in caplog.records
    )

    new_holder = IdempotencyContext(
        active=True, operation=_OPERATION, record_id=seeded.id, claim_token=new_token
    )
    await new_holder.finalize(201, {"id": "from-the-new-holder"})
    record = await _record(org_id, "fin-key")
    assert record is not None
    assert record.status == "completed"
    assert record.response_body == {"id": "from-the-new-holder"}


async def test_delete_expired_removes_only_expired_rows_in_bounded_batches():
    async with await _client() as client:
        _, org_id = await _register_admin(client, "idem-gc@example.com", "Idem Cleanup Org")
    for i in range(5):
        record = await _seed_in_progress(
            org_id, f"old-{i}", _tender_body(f"Old {i}"), lease_seconds=-1
        )
        async with UnitOfWork() as uow:
            await uow.session.execute(
                text(
                    "UPDATE idempotency_records SET expires_at = now() - interval '1 hour' "
                    "WHERE id = :id"
                ),
                {"id": record.id},
            )
            await uow.commit()
    for i in range(2):
        await _seed_in_progress(org_id, f"kept-{i}", _tender_body(f"Kept {i}"), lease_seconds=30)

    deleted_per_call = []
    for _ in range(4):
        async with UnitOfWork() as uow:
            deleted_per_call.append(await uow.idempotency.delete_expired(batch_size=2))
            await uow.commit()

    assert deleted_per_call == [2, 2, 1, 0]
    for i in range(5):
        assert await _record(org_id, f"old-{i}") is None
    for i in range(2):
        assert await _record(org_id, f"kept-{i}") is not None


async def test_delete_expired_rejects_a_non_positive_batch_size():
    async with UnitOfWork() as uow:
        with pytest.raises(ValueError):
            await uow.idempotency.delete_expired(batch_size=0)
