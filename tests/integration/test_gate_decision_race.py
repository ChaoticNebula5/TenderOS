"""Plan item 57: a decision that loses a race on a pending gate.

Two writers can decide a pending gate: another decision, or a cancel of its run
(cancel_workflow_run closes the run's pending gates in its own transaction).
The race is ordered by awaiting each step, not by sleeping:
1. The first writer's own writes are made in a transaction left open: a
   decision's gate update and GateDecisionRecorded row, or a cancel's run status
   and close_pending_for_run, as the services make them.
2. The decision starts, through the service or the HTTP route. Since PR 16
   it reads its gate with the row locked, so it waits there on the first
   writer's lock; the test waits until Postgres shows that wait.
3. The first writer commits; the decision then reads the gate as decided (or
   closed) and finishes.

Before item 57 the decision's update had no status condition. Against another
decision it overwrote the first and then failed on the outbox's unique
idempotency_key, an IntegrityError the route answered with a 500 (observed: 4 of
5 races). Against a cancel it overwrote the cancel's closing of the gate and
recorded a decision on a cancelled run. Now the update is conditional on
status = 'pending', so the decision is answered like one arriving after the
first writer: a conflict (409) when the recorded decision differs (a cancel
records none), a replay (no new row) when it is the same. Since PR 16 the
locked read decides it before the update is reached; the conditional update
stays as the backstop.
"""

import asyncio
import contextlib
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

import pytest
from _helpers import register_org_and_tender
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
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


# A query reading approval_gates FOR UPDATE that waits on a row lock: the
# decision's locked read (GateRepository.get_for_update) blocked by the first
# writer's uncommitted gate update.
_LOCKED_READ_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE '%FROM approval_gates%' AND query ILIKE '%FOR UPDATE%'"
)


async def _locked_read_waiting() -> bool:
    async with UnitOfWork() as uow:
        return (await uow.session.execute(_LOCKED_READ_WAITING)).scalar_one() > 0


async def _first_writer_wins(
    first_writer: AbstractAsyncContextManager, decision: Callable[[], Awaitable]
):
    """Run the race in the module docstring's order and return the decision's
    task (awaiting it returns its result or re-raises its exception). Waits at
    most 10 s for the decision to block on the gate's row lock; a decision that
    never takes that lock fails here instead of hanging on the first writer."""
    async with first_writer:
        loser = asyncio.create_task(decision())
        for _ in range(200):
            if loser.done() or await _locked_read_waiting():
                break
            await asyncio.sleep(0.05)
        assert not loser.done(), f"the decision ended before the first writer committed: {loser!r}"
        assert await _locked_read_waiting(), "the decision never waited on the gate's row lock"
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


async def _service_race(first: FirstWriter, decision: str):
    gate_id, decide = await _service_gate()
    loser = await _first_writer_wins(first(gate_id), lambda: decide(decision))
    return gate_id, loser


async def test_a_different_decision_that_loses_the_race_is_a_conflict():
    gate_id, loser = await _service_race(_decided("approve"), "reject")

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await loser
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_same_decision_that_loses_the_race_is_a_replay():
    gate_id, loser = await _service_race(_decided("approve"), "approve")

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


async def test_a_decision_racing_a_cancel_is_a_conflict():
    gate_id, loser = await _service_race(_cancel_held_open, "approve")

    with pytest.raises(gate_service.GateAlreadyDecidedError):
        await loser
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


# --- through the HTTP route --------------------------------------------------


@contextlib.asynccontextmanager
async def _api_gate():
    """An HTTP client, its owner's auth header, and a pending gate (with its
    run's id) on the owner's tender."""
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
        auth = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        tender = await client.post(
            f"/organizations/{registered.json()['organization_id']}/tenders",
            json={"name": "Gate Race RFP"},
            headers=auth,
        )
        gate_id = await _pending_gate(uuid.UUID(tender.json()["id"]))
        async with UnitOfWork() as uow:
            gate = await uow.gates.get(gate_id)
            assert gate is not None
            run_id = gate.workflow_run_id
        yield client, auth, gate_id, run_id


async def _api_race(first: FirstWriter, decision: str):
    async with _api_gate() as (client, auth, gate_id, _run_id):
        loser = await _first_writer_wins(
            first(gate_id),
            lambda: client.post(
                f"/gates/{gate_id}/decision",
                json={"decision": decision},
                headers={**auth, "Idempotency-Key": "race-second"},
            ),
        )
        response = await loser
    return gate_id, response


async def test_the_api_answers_409_to_a_different_decision_that_loses_the_race():
    gate_id, response = await _api_race(_decided("approve"), "reject")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GATE_ALREADY_DECIDED"
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_api_replays_the_same_decision_that_loses_the_race():
    gate_id, response = await _api_race(_decided("approve"), "approve")

    assert response.status_code == 200
    assert (response.json()["gate_id"], response.json()["status"]) == (str(gate_id), "approved")
    assert await _what_remains(gate_id) == ("approved", "approve", 1)


async def test_the_api_answers_409_to_a_decision_racing_a_cancel():
    gate_id, response = await _api_race(_cancel_held_open, "approve")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "GATE_ALREADY_DECIDED"
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


# --- a decision and a real cancel, through both routes (PR 16 review) --------
#
# Lock order: the cancel updates its run's row, then closes the run's pending
# gates; a decision locks its gate, then writes its audit row, whose foreign
# key check takes FOR KEY SHARE on the same run row. The orders differ, so
# each order is run with both sides holding their first lock: one side is
# paused at a point inside its transaction (a GateRepository method wrapped
# below), the other is started and the test waits until Postgres shows it
# waiting on a lock, then the first is released. A deadlock would make
# Postgres abort one of them, which its route answers with a 500.

# The cancel's close_pending_for_run waiting on a gate's row lock.
_GATE_UPDATE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE 'UPDATE approval_gates%'"
)


async def _waiting_on(query, task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until `query` counts a lock wait, with `task` still
    running."""
    for _ in range(200):
        if task.done():
            break
        async with UnitOfWork() as uow:
            if (await uow.session.execute(query)).scalar_one() > 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{what}: {task!r}")


def _paused(monkeypatch, method: str, *, before: bool) -> tuple[asyncio.Event, asyncio.Event]:
    """Wrap GateRepository.<method> so it stops, inside its caller's
    transaction, before or after its own statement. Returns (held, release):
    held is set when it stops, and it goes on once release is set."""
    held, release = asyncio.Event(), asyncio.Event()
    original = getattr(GateRepository, method)

    async def wrapped(self, *args, **kwargs):
        if before:
            held.set()
            await release.wait()
        result = await original(self, *args, **kwargs)
        if not before:
            held.set()
            await release.wait()
        return result

    monkeypatch.setattr(GateRepository, method, wrapped)
    return held, release


async def test_a_cancel_waits_for_a_decision_holding_the_gate_and_neither_deadlocks(monkeypatch):
    """The decision holds its gate's row lock (paused just before its update).
    The cancel updates the run's row, then waits on that gate. The decision
    then writes its audit row against the run row the cancel has updated.
    Both finish: the decision wins the gate, and the cancel cancels the run
    and leaves the decided gate as it is."""
    held, release = _paused(monkeypatch, "record_decision", before=True)
    async with _api_gate() as (client, auth, gate_id, run_id):
        decision = asyncio.create_task(
            client.post(
                f"/gates/{gate_id}/decision",
                json={"decision": "approve"},
                headers={**auth, "Idempotency-Key": "race-decision"},
            )
        )
        await asyncio.wait_for(held.wait(), 10)
        cancel = asyncio.create_task(client.post(f"/workflow-runs/{run_id}/cancel", headers=auth))
        await _waiting_on(_GATE_UPDATE_WAITING, cancel, "the cancel never waited on the gate")
        release.set()
        decided, cancelled = await asyncio.wait_for(asyncio.gather(decision, cancel), 20)

    assert (decided.status_code, decided.json()["status"]) == (200, "approved"), decided.text
    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled"), cancelled.text
    assert await _what_remains(gate_id) == ("approved", "approve", 1)
    assert not await _closed_by_the_cancel(gate_id)


async def test_a_decision_waits_for_a_cancel_holding_the_gate_and_is_refused(monkeypatch):
    """The cancel has updated the run's row and closed its pending gate
    (paused before its commit). The decision then waits on the gate's row
    lock at its read. Both finish: the cancel wins the gate, and the decision
    is answered 409, as for a decision arriving after the cancel."""
    held, release = _paused(monkeypatch, "close_pending_for_run", before=False)
    async with _api_gate() as (client, auth, gate_id, run_id):
        cancel = asyncio.create_task(client.post(f"/workflow-runs/{run_id}/cancel", headers=auth))
        await asyncio.wait_for(held.wait(), 10)
        decision = asyncio.create_task(
            client.post(
                f"/gates/{gate_id}/decision",
                json={"decision": "approve"},
                headers={**auth, "Idempotency-Key": "race-decision"},
            )
        )
        await _waiting_on(_LOCKED_READ_WAITING, decision, "the decision never waited on the gate")
        release.set()
        cancelled, decided = await asyncio.wait_for(asyncio.gather(cancel, decision), 20)

    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled"), cancelled.text
    assert decided.status_code == 409, decided.text
    assert decided.json()["error"]["code"] == "GATE_ALREADY_DECIDED"
    assert await _what_remains(gate_id) == ("rejected", None, 0)
    assert await _closed_by_the_cancel(gate_id)


# The cancel's run status update waiting on the run's row lock.
_RUN_UPDATE_WAITING = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND query ILIKE 'UPDATE workflow_runs%'"
)


async def test_a_cancel_racing_a_gate_being_opened_closes_it_too():
    """Plan item 44's order, the one the cancel's lock order exists for: a
    gate step opens a gate (GateRepository.create share-locks the run's row,
    then inserts) in a transaction left open; the cancel then waits on that
    lock at its run status update, before it closes the run's pending gates.
    Once the gate step commits, the cancel closes its gate along with the
    run's other pending one. A cancel that closed its gates first would miss
    the uncommitted gate and leave it pending on a cancelled run."""
    async with _api_gate() as (client, auth, first_gate_id, run_id):
        opened_gate_id = uuid.uuid4()
        async with UnitOfWork() as uow:
            first_gate = await uow.gates.get(first_gate_id)
            assert first_gate is not None
            await uow.gates.create(
                opened_gate_id,
                first_gate.tender_id,
                run_id,
                derive_gate_key("compliance_review", 0),
                "compliance_review",
                build_gate_payload(
                    opened_gate_id, "compliance_review", run_id, targets=[], summary={}
                ),
            )
            cancel = asyncio.create_task(
                client.post(f"/workflow-runs/{run_id}/cancel", headers=auth)
            )
            await _waiting_on(_RUN_UPDATE_WAITING, cancel, "the cancel never waited on the run")
            await uow.commit()
        cancelled = await asyncio.wait_for(cancel, 20)

    assert (cancelled.status_code, cancelled.json()["status"]) == (200, "cancelled"), cancelled.text
    assert await _closed_by_the_cancel(first_gate_id)
    assert await _closed_by_the_cancel(opened_gate_id)
