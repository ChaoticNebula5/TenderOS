"""Plan item 57: a decision that loses a race on a pending gate.

Two writers can decide a pending gate: another decision, or a cancel of its run
(cancel_workflow_run closes the run's pending gates in its own transaction).
The race is ordered by awaiting each step, not by sleeping:
1. The first writer's own writes are made in a transaction left open: a
   decision's gate update and GateDecisionRecorded row, or a cancel's run status
   and close_pending_for_run, as the services make them.
2. The decision starts, through the service or the HTTP route. It reads the
   gate while the first writer is uncommitted, so it sees it pending, and the
   test waits until it reaches its own gate update.
3. The first writer commits; the decision then finishes.

Before item 57 the decision's update had no status condition. Against another
decision it overwrote the first and then failed on the outbox's unique
idempotency_key, an IntegrityError the route answered with a 500 (observed: 4 of
5 races). Against a cancel it overwrote the cancel's closing of the gate and
recorded a decision on a cancelled run. Now the update is conditional on
status = 'pending', so the decision is answered like one arriving after the
first writer: a conflict (409) when the recorded decision differs (a cancel
records none), a replay (no new row) when it is the same.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from _helpers import register_org_and_tender
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from tenderos_api.main import app
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.repositories.gates import CLOSED_BY_CANCEL_PAYLOAD, GateRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    EVENT_GATE_DECISION_RECORDED,
    gate_decision_recorded_idempotency_key,
    gate_decision_recorded_payload,
)
from tenderos_backend.domain.services import gate_service
from tenderos_backend.orchestration.gates import (
    build_gate_payload,
    derive_gate_key,
    status_for_decision,
)

pytestmark = pytest.mark.asyncio

FirstWriter = Callable[[uuid.UUID], AbstractAsyncContextManager]


async def _pending_gate(tender_id: uuid.UUID) -> uuid.UUID:
    """A pending requirement_review gate with the request payload a real one
    has (the route builds its 200 response from it)."""
    gate_id = uuid.uuid4()
    summary = dict.fromkeys(
        (
            "requirement_count",
            "mandatory_count",
            "unverified_mandatory_count",
            "submission_item_count",
            "clarification_count",
        ),
        0,
    )
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.gates.create(
            gate_id,
            tender_id,
            run.id,
            derive_gate_key("requirement_review", 0),
            "requirement_review",
            build_gate_payload(gate_id, "requirement_review", run.id, targets=[], summary=summary),
        )
        await uow.commit()
    return gate_id


@contextlib.asynccontextmanager
async def _decision_held_open(gate_id: uuid.UUID, decision: str):
    """Another approver's decision, written as gate_service.record_decision
    writes it, in a transaction that commits only when the block ends."""
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        await uow.commit()
    async with UnitOfWork() as uow:
        gate = await uow.gates.record_decision(
            gate_id=gate_id,
            decided_by=user.id,
            status=status_for_decision(decision),
            decision_payload={"decision": decision, "notes": None, "waived": False},
        )
        assert gate is not None
        await uow.outbox.create(
            aggregate_type=AGGREGATE_APPROVAL_GATE,
            aggregate_id=gate_id,
            event_type=EVENT_GATE_DECISION_RECORDED,
            payload=gate_decision_recorded_payload(gate_id, gate.workflow_run_id),
            idempotency_key=gate_decision_recorded_idempotency_key(gate_id),
        )
        yield
        await uow.commit()


def _decided(decision: str) -> FirstWriter:
    return lambda gate_id: _decision_held_open(gate_id, decision)


@contextlib.asynccontextmanager
async def _cancel_held_open(gate_id: uuid.UUID):
    """A cancel of the gate's run, written as cancel_workflow_run writes it
    (the run's status, then its pending gates closed), in a transaction that
    commits only when the block ends."""
    async with UnitOfWork() as uow:
        gate = await uow.gates.get(gate_id)
        assert gate is not None
        assert await uow.workflow.update_run_status(gate.workflow_run_id, "cancelled")
        assert await uow.gates.close_pending_for_run(gate.workflow_run_id) == [gate_id]
        yield
        await uow.commit()


async def _first_writer_wins(
    monkeypatch, first_writer: AbstractAsyncContextManager, decision: Callable[[], Awaitable]
):
    """Run the race in the module docstring's order and return the decision's
    task (awaiting it returns its result or re-raises its exception)."""
    async with first_writer:
        at_its_update = asyncio.Event()
        record = GateRepository.record_decision

        async def signal_then_record(self, *args, **kwargs):
            at_its_update.set()
            return await record(self, *args, **kwargs)

        monkeypatch.setattr(GateRepository, "record_decision", signal_then_record)
        loser = asyncio.create_task(decision())
        reached = asyncio.create_task(at_its_update.wait())
        await asyncio.wait({loser, reached}, return_when=asyncio.FIRST_COMPLETED)
        reached.cancel()
        assert at_its_update.is_set(), f"the decision ended before its update: {loser!r}"
    return loser


async def _what_remains(gate_id: uuid.UUID) -> tuple[str, str | None, int]:
    """The gate's status, its recorded decision, and how many
    GateDecisionRecorded rows it has."""
    async with UnitOfWork() as uow:
        gate = await uow.gates.get(gate_id)
        rows = await uow.session.execute(
            select(func.count())
            .select_from(OutboxEvent)
            .where(
                OutboxEvent.aggregate_id == gate_id,
                OutboxEvent.event_type == EVENT_GATE_DECISION_RECORDED,
            )
        )
        assert gate is not None
        return gate.status, (gate.decision_payload or {}).get("decision"), rows.scalar_one()


async def _closed_by_the_cancel(gate_id: uuid.UUID) -> bool:
    async with UnitOfWork() as uow:
        gate = await uow.gates.get(gate_id)
        assert gate is not None
        return gate.decision_payload == CLOSED_BY_CANCEL_PAYLOAD


# --- through the service -----------------------------------------------------


async def _service_gate():
    email = f"race-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    gate_id = await _pending_gate(tender.id)

    def decide(decision: str):
        return gate_service.record_decision(
            gate_id, registered.organization.id, registered.user.id, decision, None
        )

    return gate_id, decide


async def _service_race(monkeypatch, first: FirstWriter, decision: str):
    gate_id, decide = await _service_gate()
    loser = await _first_writer_wins(monkeypatch, first(gate_id), lambda: decide(decision))
    return gate_id, loser


async def test_a_different_decision_that_loses_the_race_is_a_conflict(monkeypatch):
    gate_id, loser = await _service_race(monkeypatch, _decided("approve"), "reject")

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await loser
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_same_decision_that_loses_the_race_is_a_replay(monkeypatch):
    gate_id, loser = await _service_race(monkeypatch, _decided("approve"), "approve")

    gate, is_new = await loser
    assert (gate.id, gate.status, is_new) == (gate_id, "approved", False)
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_an_approve_after_a_cancel_closed_the_gate_is_a_conflict():
    gate_id, decide = await _service_gate()
    async with _cancel_held_open(gate_id):
        pass

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await decide("approve")
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


async def test_a_reject_after_a_cancel_closed_the_gate_is_a_conflict_not_a_replay():
    """A cancel leaves the gate 'rejected', the status a reject would set, but
    records no decision: the user's reject was never recorded, so it is a
    conflict, not a replay of it."""
    gate_id, decide = await _service_gate()
    async with _cancel_held_open(gate_id):
        pass

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await decide("reject")
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


async def test_a_decision_racing_a_cancel_is_a_conflict(monkeypatch):
    gate_id, loser = await _service_race(monkeypatch, _cancel_held_open, "approve")

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await loser
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


# --- through the HTTP route --------------------------------------------------


async def _api_race(monkeypatch, first: FirstWriter, decision: str):
    # Plan item 34, option 2: /auth/register needs the web client's header.
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-TenderOS-Client": "web"},
    ) as client:
        registered = await client.post(
            "/auth/register",
            json={
                "email": f"race-{uuid.uuid4().hex[:8]}@example.com",
                # A throwaway account: the token below is all the test uses.
                "password": uuid.uuid4().hex,
                "organization_name": "Gate Race Org",
            },
        )
        token = registered.json()["access_token"]
        tender = await client.post(
            f"/organizations/{registered.json()['organization_id']}/tenders",
            json={"name": "Gate Race RFP"},
            headers={"Authorization": f"Bearer {token}"},
        )
        gate_id = await _pending_gate(uuid.UUID(tender.json()["id"]))
        loser = await _first_writer_wins(
            monkeypatch,
            first(gate_id),
            lambda: client.post(
                f"/gates/{gate_id}/decision",
                json={"decision": decision},
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "race-second"},
            ),
        )
        response = await loser
    return gate_id, response


async def test_the_api_answers_409_to_a_different_decision_that_loses_the_race(monkeypatch):
    gate_id, response = await _api_race(monkeypatch, _decided("approve"), "reject")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GATE_ALREADY_DECIDED"
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_api_replays_the_same_decision_that_loses_the_race(monkeypatch):
    gate_id, response = await _api_race(monkeypatch, _decided("approve"), "approve")

    assert response.status_code == 200
    assert (response.json()["gate_id"], response.json()["status"]) == (str(gate_id), "approved")
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_api_answers_409_to_a_decision_racing_a_cancel(monkeypatch):
    gate_id, response = await _api_race(monkeypatch, _cancel_held_open, "approve")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GATE_ALREADY_DECIDED"
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)
