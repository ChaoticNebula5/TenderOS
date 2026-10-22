"""Phase 4: the async operation routes over real HTTP (routes/redteam.py's
POST .../redteam/run, routes/operations.py, including Stage 2's
GET /tenders/{id}/operations/active), plus the operation stream's
generator.

Same harness as test_workflow_api.py: httpx.AsyncClient over ASGITransport.
A stream request that is accepted opens an unbounded StreamingResponse,
which ASGITransport cannot return from (see test_workflow_events_stream.py's
docstring), so accepted tickets are checked by consuming them directly and
the stream's output is checked at generator level; rejected stream requests
answer before the generator starts and go over HTTP. No worker runs here:
operations stay queued unless a test claims them itself. One test runs the
outbox dispatcher once, with a failing enqueue, to dead-letter a request.
"""

import json
import uuid
from typing import get_args

import pytest
from httpx import ASGITransport, AsyncClient
from rq import Queue
from sqlalchemy import select, update
from tenderos_api.main import app
from tenderos_api.outbox_stream import outbox_event_stream
from tenderos_api.routes.operations import OperationKind, _operation_message
from tenderos_api.sse_tickets import (
    SCOPE_OPERATION,
    SCOPE_WORKFLOW_RUN,
    consume_sse_ticket_with_scope,
    mint_sse_ticket,
)
from tenderos_backend.db.models.async_operation import OPERATION_KINDS
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.async_operations import DISPATCH_FAILED
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    EVENT_OPERATION_REQUESTED,
    EVENT_OPERATION_STATUS_CHANGED,
)
from tenderos_backend.domain.services import operation_service
from tenderos_worker.outbox_dispatcher import MAX_ATTEMPTS, _dispatch_once

pytestmark = pytest.mark.asyncio


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _register(client: AsyncClient, email: str) -> tuple[dict[str, str], str, str]:
    # A throwaway generated password: these tests never log in again.
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": uuid.uuid4().hex, "organization_name": f"{email} org"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return (
        {"Authorization": f"Bearer {body['access_token']}"},
        body["user"]["id"],
        body["organization_id"],
    )


async def _tender(client: AsyncClient, headers: dict[str, str], org_id: str) -> str:
    response = await client.post(
        f"/organizations/{org_id}/tenders",
        json={"name": "Operations API Test Bid"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _start_red_team(client: AsyncClient, headers: dict[str, str], tender_id: str) -> dict:
    response = await client.post(f"/tenders/{tender_id}/redteam/run", headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


async def test_run_red_team_returns_202_and_a_queued_operation_it_can_read():
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-run@example.com")
        tender_id = await _tender(client, headers, org_id)

        started = await _start_red_team(client, headers, tender_id)
        assert started["status"] == "queued"
        assert started["kind"] == "redteam_run"
        assert started["tender_id"] == tender_id

        read = await client.get(f"/operations/{started['operation_id']}", headers=headers)
        assert read.status_code == 200, read.text
        assert read.json()["operation_id"] == started["operation_id"]
        assert read.json()["status"] == "queued"


async def test_a_second_run_while_one_is_active_returns_the_same_operation():
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-second@example.com")
        tender_id = await _tender(client, headers, org_id)

        first = await _start_red_team(client, headers, tender_id)
        second = await _start_red_team(client, headers, tender_id)

        assert second["operation_id"] == first["operation_id"]


async def test_a_dead_lettered_request_fails_its_operation_and_a_new_run_starts(monkeypatch):
    """Before the fix, a dead-lettered OperationRequested left its operation
    queued for good; queued counts as active, so every later POST returned
    the stuck id. The event is put one failure short of its last attempt
    and the enqueue made to fail (a non-infrastructure error, which uses up
    an attempt), so one dispatch dead-letters it."""

    def broken_enqueue(self, *args, **kwargs):
        raise ValueError("this job cannot be enqueued")

    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-dead-letter@example.com")
        tender_id = await _tender(client, headers, org_id)
        stuck_id = (await _start_red_team(client, headers, tender_id))["operation_id"]

        async with UnitOfWork() as uow:
            await uow.session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == uuid.UUID(stuck_id),
                    OutboxEvent.event_type == EVENT_OPERATION_REQUESTED,
                )
                .values(attempt_count=MAX_ATTEMPTS - 1)
            )
            await uow.commit()
        monkeypatch.setattr(Queue, "enqueue", broken_enqueue)
        await _dispatch_once()
        monkeypatch.undo()

        async with UnitOfWork() as uow:
            request_event = (
                await uow.session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == uuid.UUID(stuck_id),
                        OutboxEvent.event_type == EVENT_OPERATION_REQUESTED,
                    )
                )
            ).scalar_one()
            announced = (
                (
                    await uow.session.execute(
                        select(OutboxEvent.payload).where(
                            OutboxEvent.aggregate_id == uuid.UUID(stuck_id),
                            OutboxEvent.event_type == EVENT_OPERATION_STATUS_CHANGED,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert request_event.status == "failed"
        assert sorted(p["status"] for p in announced) == ["failed", "queued"]

        read = await client.get(f"/operations/{stuck_id}", headers=headers)
        assert read.status_code == 200, read.text
        assert read.json()["status"] == "failed"
        assert read.json()["error_code"] == DISPATCH_FAILED
        assert read.json()["error_message"]

        again = await _start_red_team(client, headers, tender_id)
        assert again["operation_id"] != stuck_id
        assert again["status"] == "queued"


async def test_another_organizations_operation_is_a_404_like_a_missing_one():
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-owner@example.com")
        tender_id = await _tender(client, headers, org_id)
        operation_id = (await _start_red_team(client, headers, tender_id))["operation_id"]
        outsider_headers, _, _ = await _register(client, "ops-api-outsider@example.com")

        cross_org = await client.get(f"/operations/{operation_id}", headers=outsider_headers)
        missing = await client.get(f"/operations/{uuid.uuid4()}", headers=outsider_headers)
        cross_org_ticket = await client.post(
            f"/operations/{operation_id}/events/ticket", headers=outsider_headers
        )

        assert cross_org.status_code == 404, cross_org.text
        assert missing.status_code == 404, missing.text
        assert cross_org.json() == missing.json()
        assert cross_org_ticket.status_code == 404, cross_org_ticket.text


async def _active(client: AsyncClient, headers: dict[str, str], tender_id: str, kind: str):
    return await client.get(
        f"/tenders/{tender_id}/operations/active", params={"kind": kind}, headers=headers
    )


async def test_the_active_operation_is_returned_while_running_and_null_when_idle():
    """Stage 2: what a screen asks on load to reattach after a reload."""
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-active@example.com")
        tender_id = await _tender(client, headers, org_id)

        for kind in OPERATION_KINDS:
            idle = await _active(client, headers, tender_id, kind)
            assert idle.status_code == 200, idle.text
            assert idle.json() is None

        started = await _start_red_team(client, headers, tender_id)
        queued = await _active(client, headers, tender_id, "redteam_run")
        assert queued.json()["operation_id"] == started["operation_id"]
        assert queued.json()["status"] == "queued"

        operation_id = uuid.UUID(started["operation_id"])
        claimed = await operation_service.claim_operation(
            operation_id, operation_service.lease_for("redteam_run")
        )
        assert claimed is not None and claimed.claim_token is not None
        running = await _active(client, headers, tender_id, "redteam_run")
        assert running.json()["operation_id"] == started["operation_id"]
        assert running.json()["status"] == "running"
        # Per kind: the tender's other kinds are still idle.
        other = await _active(client, headers, tender_id, "proposal_generation")
        assert other.json() is None

        assert await operation_service.finish_operation(
            operation_id, claimed.claim_token, "succeeded"
        )
        finished = await _active(client, headers, tender_id, "redteam_run")
        assert finished.status_code == 200, finished.text
        assert finished.json() is None


async def test_the_active_operation_is_a_403_across_organizations_and_a_404_for_a_missing_tender():
    """Tender-scoped, so it answers a non-member like every other
    tender-scoped route (require_permission): 403 ORG_ACCESS_DENIED. A
    tender that does not exist is 404. (The /operations/{id} routes keep
    their 404 for another organization's operation.)"""
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-active-owner@example.com")
        tender_id = await _tender(client, headers, org_id)
        started = await _start_red_team(client, headers, tender_id)
        outsider_headers, _, _ = await _register(client, "ops-api-active-outsider@example.com")

        cross_org = await _active(client, outsider_headers, tender_id, "redteam_run")
        missing = await _active(client, outsider_headers, str(uuid.uuid4()), "redteam_run")

        assert cross_org.status_code == 403, cross_org.text
        assert cross_org.json()["error"]["code"] == "ORG_ACCESS_DENIED"
        assert started["operation_id"] not in cross_org.text
        assert missing.status_code == 404, missing.text


async def test_the_active_operation_needs_a_known_kind():
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-active-kind@example.com")
        tender_id = await _tender(client, headers, org_id)

        unknown = await _active(client, headers, tender_id, "not_a_kind")
        no_kind = await client.get(f"/tenders/{tender_id}/operations/active", headers=headers)

        assert unknown.status_code == 422, unknown.text
        assert no_kind.status_code == 422, no_kind.text
    # The route's accepted kinds are the model's kinds, no more, no fewer.
    assert set(get_args(OperationKind)) == set(OPERATION_KINDS)


async def test_a_minted_operation_ticket_is_scoped_to_its_operation_and_single_use():
    async with await _client() as client:
        headers, user_id, org_id = await _register(client, "ops-api-ticket@example.com")
        tender_id = await _tender(client, headers, org_id)
        operation_id = (await _start_red_team(client, headers, tender_id))["operation_id"]

        mint = await client.post(f"/operations/{operation_id}/events/ticket", headers=headers)
        assert mint.status_code == 200, mint.text
        ticket = mint.json()["ticket"]

        resolved = await consume_sse_ticket_with_scope(ticket)
        assert resolved is not None
        resolved_user, scope, resource = resolved
        assert (str(resolved_user), scope, str(resource)) == (
            user_id,
            SCOPE_OPERATION,
            operation_id,
        )

        # Consumed: the stream refuses it.
        reused = await client.get(
            f"/operations/{operation_id}/events/stream", params={"ticket": ticket}
        )
        assert reused.status_code == 401, reused.text


async def test_a_ticket_for_another_stream_is_refused():
    async with await _client() as client:
        headers, user_id, org_id = await _register(client, "ops-api-scope@example.com")
        tender_id = await _tender(client, headers, org_id)
        operation_id = (await _start_red_team(client, headers, tender_id))["operation_id"]

        # A workflow-run ticket bound to the same id: wrong scope.
        workflow_ticket = await mint_sse_ticket(
            uuid.UUID(user_id), uuid.UUID(operation_id), SCOPE_WORKFLOW_RUN
        )
        wrong_scope = await client.get(
            f"/operations/{operation_id}/events/stream", params={"ticket": workflow_ticket}
        )
        assert wrong_scope.status_code == 401, wrong_scope.text

        # An operation ticket for a different operation.
        other_ticket = await mint_sse_ticket(uuid.UUID(user_id), uuid.uuid4(), SCOPE_OPERATION)
        wrong_operation = await client.get(
            f"/operations/{operation_id}/events/stream", params={"ticket": other_ticket}
        )
        assert wrong_operation.status_code == 401, wrong_operation.text

        # And an operation ticket is no good on a workflow run's stream.
        op_ticket = await mint_sse_ticket(
            uuid.UUID(user_id), uuid.UUID(operation_id), SCOPE_OPERATION
        )
        on_workflow = await client.get(
            f"/workflow-runs/{operation_id}/events/stream", params={"ticket": op_ticket}
        )
        assert on_workflow.status_code == 401, on_workflow.text


async def test_the_operation_stream_sends_a_heartbeat_then_each_status_reached():
    async with await _client() as client:
        headers, _, org_id = await _register(client, "ops-api-stream@example.com")
        tender_id = await _tender(client, headers, org_id)
        operation_id = uuid.UUID(
            (await _start_red_team(client, headers, tender_id))["operation_id"]
        )
        other_tender = await _tender(client, headers, org_id)
        other_id = uuid.UUID((await _start_red_team(client, headers, other_tender))["operation_id"])

    # The same generator stream_operation_events returns.
    gen = outbox_event_stream(
        event_types=[EVENT_OPERATION_STATUS_CHANGED],
        payload_key="operation_id",
        resource_id=operation_id,
        to_message=_operation_message,
    )
    # 'queued' was announced before the stream opened: not replayed.
    assert await anext(gen) == ": heartbeat\n\n"

    # Another operation's progress is not this stream's.
    assert await operation_service.claim_operation(
        other_id, operation_service.lease_for("redteam_run")
    )
    claimed = await operation_service.claim_operation(
        operation_id, operation_service.lease_for("redteam_run")
    )
    assert claimed is not None and claimed.claim_token is not None

    chunk = await anext(gen)
    assert chunk.startswith("data: ")
    message = json.loads(chunk.removeprefix("data: ").strip())
    assert message["event_type"] == "operation.status_changed"
    assert message["operation_id"] == str(operation_id)
    assert message["status"] == "running"

    assert await operation_service.finish_operation(operation_id, claimed.claim_token, "succeeded")
    message = json.loads((await anext(gen)).removeprefix("data: ").strip())
    assert message["status"] == "succeeded"
    await gen.aclose()
