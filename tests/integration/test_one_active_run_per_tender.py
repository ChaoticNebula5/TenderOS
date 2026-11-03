"""Phase 4 plan item 43: at most one active workflow run per tender, enforced
by the database (uq_workflow_runs_one_active_per_tender, migration 022) and
answered as WorkflowRunActiveError (the route's 409) instead of a 500.

Active means queued, running or waiting_for_human (ACTIVE_RUN_STATUSES); a
run with a retry scheduled stays running, so it counts. Terminal runs
(completed, failed, cancelled) are outside the index.
"""

import asyncio
import uuid

import pytest
from _helpers import make_ready_run_inputs
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.workflow import WorkflowRun
from tenderos_backend.db.repositories.workflow import WorkflowRepository
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.events.outbox_events import EVENT_WORKFLOW_RUN_CREATED
from tenderos_backend.domain.services import workflow_service

pytestmark = pytest.mark.asyncio

_WAIT_SECONDS = 30
_INDEX = "uq_workflow_runs_one_active_per_tender"


async def _ready_tender(label: str):
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    registered, tender = await make_ready_run_inputs(email, f"{email} org", f"{email} tender")
    return registered, tender


def _start(registered, tender):
    return workflow_service.create_workflow_run(
        tender_id=tender.id,
        organization_id=registered.organization.id,
        created_by=registered.user.id,
    )


async def _counts(tender_id: uuid.UUID) -> tuple[int, int]:
    """(runs for the tender, WorkflowRunCreated events for those runs)."""
    async with UnitOfWork() as uow:
        runs = (
            await uow.session.execute(
                select(func.count()).where(WorkflowRun.tender_id == tender_id)
            )
        ).scalar_one()
        created = (
            await uow.session.execute(
                select(func.count())
                .select_from(OutboxEvent)
                .join(WorkflowRun, WorkflowRun.id == OutboxEvent.aggregate_id)
                .where(
                    WorkflowRun.tender_id == tender_id,
                    OutboxEvent.event_type == EVENT_WORKFLOW_RUN_CREATED,
                )
            )
        ).scalar_one()
    return runs, created


def _insert_together(monkeypatch: pytest.MonkeyPatch, parties: int) -> None:
    """Every create waits at a barrier just before its insert, so all
    `parties` calls have passed the documents check and insert at the same
    moment on separate connections, instead of one after another by luck."""
    barrier = asyncio.Barrier(parties)
    original = WorkflowRepository.create_run

    async def create_together(self, **kwargs):
        await asyncio.wait_for(barrier.wait(), timeout=_WAIT_SECONDS)
        return await original(self, **kwargs)

    monkeypatch.setattr(WorkflowRepository, "create_run", create_together)


async def test_two_starts_at_once_make_one_run_and_one_refusal(monkeypatch):
    """Two tabs click Start processing at the same moment, each with its own
    Idempotency-Key. Exactly one run; the other call is refused naming it.
    Repeated, since one pass could pass by luck."""
    for _ in range(5):
        registered, tender = await _ready_tender("race")
        _insert_together(monkeypatch, 2)

        results = await asyncio.wait_for(
            asyncio.gather(
                _start(registered, tender), _start(registered, tender), return_exceptions=True
            ),
            timeout=_WAIT_SECONDS,
        )
        monkeypatch.undo()

        runs = [r for r in results if isinstance(r, WorkflowRun)]
        refusals = [r for r in results if isinstance(r, workflow_service.WorkflowRunActiveError)]
        assert len(runs) == 1 and len(refusals) == 1, results
        assert refusals[0].active_run_id == runs[0].id
        assert await _counts(tender.id) == (1, 1)


async def test_the_index_refuses_a_second_active_run_inserted_directly():
    registered, tender = await _ready_tender("direct")
    first = await _start(registered, tender)

    async with UnitOfWork() as uow:
        with pytest.raises(IntegrityError, match=_INDEX):
            await uow.workflow.create_run(
                tender_id=tender.id, prompt_bundle_version="test", model_config={}
            )

    assert await _counts(tender.id) == (1, 1)
    async with UnitOfWork() as uow:
        assert (await uow.workflow.get_active_run_for_tender(tender.id)).id == first.id


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_a_tender_whose_run_finished_can_start_another(terminal):
    registered, tender = await _ready_tender(f"after-{terminal}")
    first = await _start(registered, tender)
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(first.id, terminal)
        await uow.commit()

    second = await _start(registered, tender)

    assert second.id != first.id
    assert await _counts(tender.id) == (2, 2)


async def test_a_write_reviving_a_cancelled_run_fails_while_a_newer_run_is_active():
    """The known cancellation race (KNOWN_LIMITATIONS, "Cancellation-during-
    execution race, remaining half"): a node still running in a cancelled
    run's job can write a non-terminal status over "cancelled". With a newer
    run active, the index now refuses that write instead of leaving two
    runs active; the cancelled run stays cancelled."""
    registered, tender = await _ready_tender("revive")
    old = await _start(registered, tender)
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(old.id, "cancelled")
        await uow.commit()
    new = await _start(registered, tender)

    with pytest.raises(IntegrityError, match=_INDEX):
        await workflow_service.sync_run_status(old.id, "drafting")

    async with UnitOfWork() as uow:
        assert (await uow.workflow.get_run(old.id)).status == "cancelled"
        assert (await uow.workflow.get_active_run_for_tender(tender.id)).id == new.id


async def test_a_different_violation_is_not_reported_as_an_active_run(monkeypatch):
    """Plan item 44, from item 43: only a violation of the one-active-run
    index means "a run is already active". Here the tender does have an
    active run, but the insert fails on another constraint (a status the
    CHECK refuses, which the partial index does not cover): the driver's
    error is re-raised as it is, not read as the active run."""
    registered, tender = await _ready_tender("other-violation")
    active = await _start(registered, tender)

    async def insert_a_refused_status(self, **kwargs):
        run = WorkflowRun(
            tender_id=kwargs["tender_id"],
            langgraph_thread_id=str(uuid.uuid4()),
            status="not-a-status",
            prompt_bundle_version=kwargs["prompt_bundle_version"],
            model_config_json=kwargs["model_config"],
        )
        self._session.add(run)
        await self._session.flush()
        return run

    monkeypatch.setattr(WorkflowRepository, "create_run", insert_a_refused_status)

    with pytest.raises(IntegrityError, match="ck_workflow_run_status"):
        await _start(registered, tender)

    async with UnitOfWork() as uow:
        assert (await uow.workflow.get_active_run_for_tender(tender.id)).id == active.id
