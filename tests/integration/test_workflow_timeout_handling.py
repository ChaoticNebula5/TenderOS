"""B17: RQ's own rq.timeouts.JobTimeoutException kills a job by interrupting
asyncio's event loop itself (confirmed against a real stuck run: the
traceback bottoms out inside asyncio's own _run_once/selectors.select, deep
inside rq/job.py, not at any awaited line inside run_workflow's own try
block) - which meant run_workflow/resume_workflow/retry_workflow_run's own
`except Exception` never ran, leaving workflow_runs.status stuck at
"running" forever (verified directly against a real row: status='running',
completed_at=NULL, retry_count=0, hours after the job died).

Two things to prove, real Postgres, no LLM cost:
1. mark_run_failed_on_timeout actually marks the run failed and leaves a
   distinguishable audit trail (KNOWN_LIMITATIONS.md's own point: a normal
   graph failure writes no audit event describing why at all - so any audit
   event here is unique to this path by construction).
2. _run_sync - the thing RQ now calls directly instead of the bare async
   functions - actually catches JobTimeoutException and calls (1), rather
   than letting it vanish the way it did for the run that exposed this.
"""

import asyncio
import uuid

import pytest
from rq.timeouts import JobTimeoutException
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import engine, get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services.workflow_service import mark_run_failed_on_timeout
from tenderos_worker.jobs.workflow import _run_sync


def _reset_engine_for_new_loop() -> None:
    """Every asyncio.run() boundary below (this test file's own setup/check
    calls, on top of _run_sync's internal one(s)) shares db/session.py's
    module-level `engine` singleton across a freshly created loop each
    time. Confirmed directly, not assumed: without this, the FIRST such
    boundary (plain setup, no JobTimeoutException involved at all) already
    raised "Future attached to a different loop" from asyncpg deep inside
    the pool's own connection-cleanup path - not unique to _run_sync's
    timeout-recovery branch, a general hazard of reusing this engine
    across more than one asyncio.run() call in the same process.
    AsyncEngine.dispose() (SQLAlchemy's own documented remedy for exactly
    this) resets the pool so the next loop opens fresh connections.
    """
    asyncio.run(engine.dispose())


async def _make_org_tender_and_run(name: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"{name} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name=f"{name} Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        org_id, tender_id = org.id, tender.id
    async with UnitOfWork() as uow:
        run = await uow.workflow.create_run(tender_id, "v1", {})
        await uow.workflow.update_run_status(run.id, "running")
        await uow.commit()
        run_id = run.id
    return org_id, run_id


@pytest.mark.asyncio
async def test_mark_run_failed_on_timeout_flips_status_and_leaves_a_distinguishable_audit_trail():
    _org_id, run_id = await _make_org_tender_and_run("TimeoutMark")

    await mark_run_failed_on_timeout(run_id)

    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        assert run is not None
        assert run.status == "failed"
        # Plan item 19: the reason the screen shows under "Last run".
        assert run.error_code == "JOB_TIMEOUT"

        events = await uow.audit.list_for_workflow_run(run_id)
    timeout_events = [e for e in events if e.action == "workflow_run_timed_out"]
    assert len(timeout_events) == 1
    assert timeout_events[0].event_metadata == {"error_code": "job_timeout"}
    assert timeout_events[0].actor_type == "system"


@pytest.mark.asyncio
async def test_mark_run_failed_on_timeout_is_a_noop_for_a_run_that_no_longer_exists():
    # No exception, no orphaned write - the same "don't assume the row is
    # still there" caution _handle_graph_failure's own docstring names for
    # a run that vanished between when the job started and when this fires.
    await mark_run_failed_on_timeout(uuid.uuid4())


@pytest.mark.asyncio
async def test_mark_run_failed_on_timeout_does_not_overwrite_a_real_cancellation():
    # CodeRabbit-caught race: cancel_workflow_run can flip a run to
    # "cancelled" after RQ's death penalty has already started killing the
    # same job, and before mark_run_failed_on_timeout runs. The old
    # get()-then-write (update_run_status) would silently stomp "cancelled"
    # back to "failed" and back it with a false "workflow_run_timed_out"
    # audit event - this proves mark_failed_if_not_terminal's WHERE clause
    # actually stops that, against a real Postgres row already sitting at
    # "cancelled" when this fires.
    _org_id, run_id = await _make_org_tender_and_run("TimeoutMarkCancelled")
    async with UnitOfWork() as uow:
        await uow.workflow.update_run_status(run_id, "cancelled")
        await uow.commit()

    await mark_run_failed_on_timeout(run_id)

    async with UnitOfWork() as uow:
        run = await uow.workflow.get_run(run_id)
        assert run is not None
        assert run.status == "cancelled"

        events = await uow.audit.list_for_workflow_run(run_id)
    timeout_events = [e for e in events if e.action == "workflow_run_timed_out"]
    assert len(timeout_events) == 0


# The two tests below are deliberately plain sync functions, not
# @pytest.mark.asyncio - _run_sync's whole reason for existing is that it
# owns the sync-to-async transition itself (asyncio.run(coro)), the same
# transition RQ performs when it calls a worker's job function from a plain
# synchronous context with no event loop running yet. Calling _run_sync
# from inside an already-running pytest-asyncio event loop hits the exact
# thing asyncio.run() itself refuses to do ("cannot be called from a
# running event loop") - confirmed directly, not assumed, by trying it
# first and getting that RuntimeError. Setup/assertions still need async
# UnitOfWork calls, so each wraps its own asyncio.run() around just those,
# separately from _run_sync's own.


def test_run_sync_catches_job_timeout_marks_the_run_failed_and_reraises():
    # Reset before this test's own first use too, not just between its
    # internal boundaries - the tests above run inside pytest-asyncio's own
    # managed loop (@pytest.mark.asyncio), which can leave pooled
    # connections bound to THAT loop by the time this test's own fresh
    # asyncio.run() starts.
    _reset_engine_for_new_loop()
    _org_id, run_id = asyncio.run(_make_org_tender_and_run("TimeoutRunSync"))
    _reset_engine_for_new_loop()

    async def _never_finishes() -> None:
        raise JobTimeoutException("Task exceeded maximum timeout value (1800 seconds)")

    with pytest.raises(JobTimeoutException):
        _run_sync(_never_finishes(), run_id)
    _reset_engine_for_new_loop()

    async def _check() -> str:
        async with UnitOfWork() as uow:
            run = await uow.workflow.get_run(run_id)
            assert run is not None
            return run.status

    assert asyncio.run(_check()) == "failed"


def test_run_sync_lets_a_normal_exception_propagate_without_touching_the_run():
    # run_workflow/resume_workflow/retry_workflow_run's own except blocks
    # already handle every non-timeout failure correctly (mark failed or
    # schedule a retry, then re-raise) - _run_sync must not do anything
    # extra for those, or a run that's already correctly marked (or
    # correctly left alone pending a scheduled retry) would get silently
    # overwritten to "failed" a second time by the wrong code path.
    _reset_engine_for_new_loop()
    _org_id, run_id = asyncio.run(_make_org_tender_and_run("TimeoutRunSyncOrdinary"))
    _reset_engine_for_new_loop()

    async def _ordinary_failure() -> None:
        raise ValueError("not a timeout")

    with pytest.raises(ValueError):
        _run_sync(_ordinary_failure(), run_id)
    _reset_engine_for_new_loop()

    async def _check() -> str:
        async with UnitOfWork() as uow:
            run = await uow.workflow.get_run(run_id)
            assert run is not None
            return run.status

    # _run_sync itself must not have touched status - still "running" (this
    # test's own setup value), not "failed".
    assert asyncio.run(_check()) == "running"
