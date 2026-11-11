"""Plan item 55 (decision 0082): outbox retention against real Postgres.

OutboxRepository.delete_old_dispatched and delete_old_dead_letters, and the
outbox dispatcher's cleanups that call them once per cleanup tick. The rules:
a dispatched event goes after outbox_dispatched_retention_days, a dead letter
after outbox_dead_letter_retention_days (from its last scheduled attempt), a
pending event never, and a gate decision is kept while its run still waits.
Each call deletes at most one batch.

Rows are aged with the database clock (func.now() minus an interval). Some
carry timestamps their status would not normally have (a pending row with
an old dispatched_at): that is deliberate, so the status rule is tested on
its own and not hidden behind the age rule.
"""

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

import pytest
from _helpers import register_org_and_tender
from sqlalchemy import func, select, update
from tenderos_backend.core.config import settings as backend_settings
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.repositories.outbox import OutboxRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import (
    AGGREGATE_APPROVAL_GATE,
    EVENT_GATE_DECISION_RECORDED,
    gate_decision_recorded_payload,
)
from tenderos_worker import outbox_dispatcher

pytestmark = pytest.mark.asyncio

DAY = timedelta(days=1)
WEEK = timedelta(days=7)
MONTH = timedelta(days=30)


async def _event(
    status: str,
    *,
    dispatched_ago: timedelta | None = None,
    available_ago: timedelta | None = None,
    aggregate_type: str = "tender",
    aggregate_id: uuid.UUID | None = None,
    event_type: str = "TenderCreated",
) -> uuid.UUID:
    async with UnitOfWork() as uow:
        event = await uow.outbox.create(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id or uuid.uuid4(),
            event_type=event_type,
            payload={},
            idempotency_key=f"retention-{uuid.uuid4()}",
        )
        values: dict = {"status": status}
        if dispatched_ago is not None:
            values["dispatched_at"] = func.now() - dispatched_ago
        if available_ago is not None:
            values["available_at"] = func.now() - available_ago
        await uow.session.execute(
            update(OutboxEvent).where(OutboxEvent.id == event.id).values(**values)
        )
        await uow.commit()
        return event.id


async def _surviving(*event_ids: uuid.UUID) -> set[uuid.UUID]:
    async with UnitOfWork() as uow:
        rows = await uow.session.execute(
            select(OutboxEvent.id).where(OutboxEvent.id.in_(event_ids))
        )
        return set(rows.scalars().all())


async def _delete_dispatched(older_than: timedelta = WEEK, batch_size: int = 500) -> int:
    async with UnitOfWork() as uow:
        deleted = await uow.outbox.delete_old_dispatched(older_than, batch_size)
        await uow.commit()
        return deleted


async def _delete_dead_letters(older_than: timedelta = MONTH, batch_size: int = 500) -> int:
    async with UnitOfWork() as uow:
        deleted = await uow.outbox.delete_old_dead_letters(older_than, batch_size)
        await uow.commit()
        return deleted


async def _gate_decision(run_status: str, dispatched_ago: timedelta) -> uuid.UUID:
    """A decided gate on a run in `run_status`, with its GateDecisionRecorded
    event dispatched `dispatched_ago`. Returns the event id."""
    email = f"retention-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    gate_id = uuid.uuid4()
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender.id, "v1", {})
        await uow.gates.create(
            gate_id, tender.id, run.id, "requirement_review:plan-1", "requirement_review", {}
        )
        await uow.session.execute(
            update(ApprovalGate)
            .where(ApprovalGate.id == gate_id)
            .values(
                status="approved", decision_payload={"decision": "approve"}, decided_at=func.now()
            )
        )
        await uow.session.execute(
            update(WorkflowRun).where(WorkflowRun.id == run.id).values(status=run_status)
        )
        event = await uow.outbox.create(
            aggregate_type=AGGREGATE_APPROVAL_GATE,
            aggregate_id=gate_id,
            event_type=EVENT_GATE_DECISION_RECORDED,
            payload=gate_decision_recorded_payload(gate_id, run.id),
            idempotency_key=f"retention-gate-{gate_id}",
        )
        await uow.session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event.id)
            .values(status="dispatched", dispatched_at=func.now() - dispatched_ago)
        )
        await uow.commit()
        return event.id


async def _waiting_run_with_gates(
    *gates: tuple[str, timedelta],
) -> tuple[uuid.UUID, list[uuid.UUID], dict[uuid.UUID, uuid.UUID]]:
    """A waiting_for_human run with a current job (so the gate backstop
    considers it), with one gate per (status, requested_ago). Every decided
    gate has its GateDecisionRecorded dispatched a month ago. Gates given the
    same requested_ago get the same requested_at (one transaction, one
    now()), so their id decides which is newest. Returns the run id, the gate
    ids in the order given, and each decided gate's event id."""
    email = f"retention-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    gate_ids = [uuid.uuid4() for _ in gates]
    events: dict[uuid.UUID, uuid.UUID] = {}
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender.id, "v1", {})
        for gate_id, (status, requested_ago) in zip(gate_ids, gates, strict=True):
            await uow.gates.create(
                gate_id,
                tender.id,
                run.id,
                f"requirement_review:plan-{gate_id.hex[:8]}",
                "requirement_review",
                {},
            )
            values: dict = {"status": status, "requested_at": func.now() - requested_ago}
            if status != "pending":
                values.update(decision_payload={"decision": "approve"}, decided_at=func.now())
            await uow.session.execute(
                update(ApprovalGate).where(ApprovalGate.id == gate_id).values(**values)
            )
            if status == "pending":
                continue
            event = await uow.outbox.create(
                aggregate_type=AGGREGATE_APPROVAL_GATE,
                aggregate_id=gate_id,
                event_type=EVENT_GATE_DECISION_RECORDED,
                payload=gate_decision_recorded_payload(gate_id, run.id),
                idempotency_key=f"retention-gate-{gate_id}",
            )
            await uow.session.execute(
                update(OutboxEvent)
                .where(OutboxEvent.id == event.id)
                .values(status="dispatched", dispatched_at=func.now() - MONTH)
            )
            events[gate_id] = event.id
        await uow.session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == run.id)
            .values(status="waiting_for_human", current_job_id=f"resume-{run.id}")
        )
        await uow.commit()
        return run.id, gate_ids, events


async def _gates_the_backstop_reads(*run_ids: uuid.UUID) -> set[tuple[uuid.UUID, uuid.UUID]]:
    async with UnitOfWork() as uow:
        at_gate = await uow.workflow.list_waiting_runs_with_a_decided_gate(500)
    return {(r.run_id, r.gate_id) for r in at_gate if r.run_id in run_ids}


# --- the rules ---------------------------------------------------------------


async def test_a_dispatched_event_goes_after_the_period_and_a_younger_one_stays():
    old = await _event("dispatched", dispatched_ago=WEEK + DAY)
    young = await _event("dispatched", dispatched_ago=WEEK - DAY)

    assert await _delete_dispatched() == 1
    assert await _surviving(old, young) == {young}


async def test_a_dead_letter_goes_after_its_own_period_from_its_last_attempt():
    old = await _event("failed", available_ago=MONTH + DAY)
    young = await _event("failed", available_ago=MONTH - DAY)

    assert await _delete_dead_letters() == 1
    assert await _surviving(old, young) == {young}


async def test_each_cleanup_takes_only_its_own_status():
    """The dispatched cleanup never takes a dead letter, however old its
    timestamps, and the dead-letter cleanup never takes a dispatched event."""
    dead_letter = await _event("failed", dispatched_ago=MONTH * 12, available_ago=WEEK + DAY)
    dispatched = await _event("dispatched", dispatched_ago=WEEK + DAY, available_ago=MONTH * 12)

    assert await _delete_dispatched() == 1
    assert await _delete_dead_letters() == 0
    assert await _surviving(dead_letter, dispatched) == {dead_letter}


async def test_a_pending_event_is_never_deleted_however_old():
    pending = await _event("pending", dispatched_ago=MONTH * 12, available_ago=MONTH * 12)

    assert await _delete_dispatched() == 0
    assert await _delete_dead_letters() == 0
    assert await _surviving(pending) == {pending}


async def test_a_gate_decision_stays_while_its_run_waits_and_goes_once_it_does_not():
    waiting = await _gate_decision("waiting_for_human", dispatched_ago=MONTH)
    finished = await _gate_decision("completed", dispatched_ago=MONTH)

    assert await _delete_dispatched() == 1
    assert await _surviving(waiting, finished) == {waiting}


async def test_only_the_decision_for_the_gate_a_waiting_run_is_at_now_is_kept():
    """A waiting run keeps only its newest gate's decision, newest in the
    gate backstop's own order (requested_at, then id), and that is exactly
    the row list_waiting_runs_with_a_decided_gate reads. A decision for a gate
    the run has already passed goes."""
    # A: two decided gates a day apart; the later one is where the run is.
    run_a, (a_passed, a_now), events_a = await _waiting_run_with_gates(
        ("approved", DAY * 2), ("approved", DAY)
    )
    # B: a decided gate, then a newer pending one: nothing decided to keep.
    run_b, (b_passed, _b_pending), events_b = await _waiting_run_with_gates(
        ("approved", DAY * 2), ("pending", DAY)
    )
    # C: two decided gates with the same requested_at: the larger id is newest.
    run_c, gates_c, events_c = await _waiting_run_with_gates(("approved", DAY), ("approved", DAY))
    c_now, c_passed = max(gates_c), min(gates_c)

    read_by_the_backstop = {(run_a, a_now), (run_c, c_now)}
    assert await _gates_the_backstop_reads(run_a, run_b, run_c) == read_by_the_backstop

    assert await _delete_dispatched() == 3
    assert await _surviving(*events_a.values(), *events_b.values(), *events_c.values()) == {
        events_a[a_now],
        events_c[c_now],
    }
    assert events_b.keys() == {b_passed} and c_passed in events_c and a_passed in events_a
    # What retention kept is what the backstop still reads.
    assert await _gates_the_backstop_reads(run_a, run_b, run_c) == read_by_the_backstop


async def test_one_call_deletes_at_most_one_batch():
    old = [await _event("dispatched", dispatched_ago=WEEK + DAY * (i + 1)) for i in range(5)]

    assert await _delete_dispatched(batch_size=2) == 2
    # Oldest first: the two oldest went.
    assert await _surviving(*old) == set(old[:3])
    assert await _delete_dispatched(batch_size=2) == 2
    assert await _delete_dispatched(batch_size=2) == 1
    assert await _delete_dispatched(batch_size=2) == 0


async def test_a_batch_size_below_one_is_refused():
    async with UnitOfWork() as uow:
        with pytest.raises(ValueError):
            await uow.outbox.delete_old_dispatched(WEEK, 0)
        with pytest.raises(ValueError):
            await uow.outbox.delete_old_dead_letters(MONTH, 0)


# --- the dispatcher's cleanups ------------------------------------------------


async def test_the_cleanups_use_the_settings_and_log_what_they_deleted(monkeypatch, caplog):
    """Two periods far enough apart that each row is on opposite sides of
    them: a cleanup reading the other kind's period gets the wrong answer."""
    monkeypatch.setattr(backend_settings, "outbox_dispatched_retention_days", 1)
    monkeypatch.setattr(backend_settings, "outbox_dead_letter_retention_days", 5)
    dispatched = await _event("dispatched", dispatched_ago=DAY * 3)  # past 1 day, not 5
    dead_letter = await _event("failed", available_ago=DAY * 6)  # past 5 days
    young_dead_letter = await _event("failed", available_ago=DAY * 3)  # past 1 day, not 5

    with caplog.at_level(logging.INFO, logger=outbox_dispatcher.logger.name):
        await outbox_dispatcher._delete_old_dispatched_events()
        await outbox_dispatcher._delete_old_dead_letters()

    assert await _surviving(dispatched, dead_letter, young_dead_letter) == {young_dead_letter}
    messages = [r.getMessage() for r in caplog.records]
    assert "deleted 1 dispatched outbox events older than 1 days" in messages
    assert "deleted 1 dead-lettered outbox events older than 5 days" in messages


async def test_each_tick_runs_one_capped_batch_of_each_and_a_failure_stops_nothing(monkeypatch):
    """On every cleanup tick the loop asks for exactly one batch of each kind,
    of CLEANUP_BATCH_SIZE (500) rows; one cleanup raising neither stops the
    loop nor skips the other."""
    monkeypatch.setattr(outbox_dispatcher, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(outbox_dispatcher, "CLEANUP_EVERY_N_TICKS", 1)
    ticks: list[int] = []
    calls: dict[str, list[int]] = {"dispatched": [], "dead_letters": []}

    async def counted_tick(queues=None):
        ticks.append(1)

    async def broken_dispatched(self, older_than, batch_size):
        calls["dispatched"].append(batch_size)
        raise RuntimeError("dispatched cleanup broke")

    async def counted_dead_letters(self, older_than, batch_size):
        assert older_than == timedelta(days=backend_settings.outbox_dead_letter_retention_days)
        calls["dead_letters"].append(batch_size)
        return 0

    monkeypatch.setattr(outbox_dispatcher, "_dispatch_once", counted_tick)
    monkeypatch.setattr(OutboxRepository, "delete_old_dispatched", broken_dispatched)
    monkeypatch.setattr(OutboxRepository, "delete_old_dead_letters", counted_dead_letters)

    loop_task = asyncio.create_task(outbox_dispatcher._run_forever())
    try:
        deadline = asyncio.get_running_loop().time() + 10
        while len(calls["dead_letters"]) < 3:
            assert not loop_task.done(), f"the loop stopped: {loop_task.exception()!r}"
            assert asyncio.get_running_loop().time() < deadline, "the cleanups did not run"
            await asyncio.sleep(0.02)
        assert not loop_task.done(), "the loop must still be running"
    finally:
        loop_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop_task

    # One batch of each per tick, never more, each capped at the batch size.
    assert len(calls["dispatched"]) <= len(ticks) and len(calls["dead_letters"]) <= len(ticks)
    assert abs(len(calls["dispatched"]) - len(calls["dead_letters"])) <= 1
    assert set(calls["dispatched"]) == {outbox_dispatcher.CLEANUP_BATCH_SIZE} == {500}
    assert set(calls["dead_letters"]) == {500}
