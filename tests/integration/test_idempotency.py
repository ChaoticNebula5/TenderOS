import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from tenderos_api.main import app

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


async def test_concurrent_duplicate_idempotency_key_converges_to_one_tender_not_two():
    """B16: the sequential test above (same_body_replays_response) proves
    retry-safety - the second call sees a COMPLETED record and replays it.
    It says nothing about genuine concurrency: two requests that both reach
    get_idempotency_context's own uow.idempotency.get(...) before either
    has committed a row would both see None and both attempt create_
    in_progress, the loser hitting uq_idempotency_key - the same
    "sequential retry tested, concurrency untested" shape already found for
    document_service.create_document. asyncio.gather against the real ASGI
    app, not a sequential await, so both requests are genuinely in flight
    together.

    What the fix actually guarantees, found to be narrower than first
    assumed by this test itself: NOT "both succeed with the same tender" -
    unlike create_document's own race (where a second, independent content-
    hash constraint lets the loser converge on the winner's FINISHED row),
    here the loser's reread finds only a marker that a sibling is
    currently, actively doing the work - the tender doesn't exist yet to
    converge on. create_tender has no uniqueness constraint of its own two
    concurrent creates could otherwise collide on. So the real guarantee is
    narrower but still real: exactly one request creates the tender: the
    other gets a clean 409 IDEMPOTENCY_CONFLICT, never a 500, and never a
    second, silently-duplicated tender.
    """
    async with await _client() as client:
        token, org_id = await _register_admin(client, "idem-concurrent@example.com", "Idem Concurrent Org")
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "concurrent-create-tender"}
        body = {"name": "Concurrent Idempotent RFP"}

        first, second = await asyncio.gather(
            client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers),
            client.post(f"/organizations/{org_id}/tenders", json=body, headers=headers),
        )

        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [201, 409], (
            f"expected exactly one 201 and one 409 (IDEMPOTENCY_CONFLICT), "
            f"got {first.status_code}: {first.text} / {second.status_code}: {second.text}"
        )
        conflict = first if first.status_code == 409 else second
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

        listing = await client.get(
            f"/organizations/{org_id}/tenders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert len(listing.json()) == 1, "the race must not have created a duplicate tender"


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
