"""Plan item 124: the reanalysis claim reaper ends a running claim whose job
will never finish it, and never one whose job may still run.

Real RQ on the test Redis's workflow queue and real Postgres, with the run
reaper's own registry states (_rq_registry.py) and the dispatcher's own RQ
check (outbox_dispatcher.RunJobInspector): reanalysis jobs are on the same
queue. The rule and the writes are reanalysis_service's.

Other tests leave claims running with jobs RQ never had, and a reap pass
ends those too, so these tests look only at their own claims, and pass a
batch size large enough to reach them.
"""

import logging
import types
import uuid

import pytest
from _helpers import register_org_and_tender
from _rq_registry import Clock, RqRegistry
from redis import Redis
from sqlalchemy import select, text, update
from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.run_endings import (
    JOB_ABANDONED,
    JOB_CANCELED,
    JOB_FAILED,
    JOB_FINISHED,
    JOB_UNKNOWN,
)
from tenderos_backend.domain.services import reanalysis_service
from tenderos_worker import outbox_dispatcher
from tenderos_worker.outbox_dispatcher import RunJobInspector
from tenderos_worker.settings import settings

pytestmark = pytest.mark.asyncio

_EVERY_CLAIM = 10_000
_DEAD_REDIS_URL = "redis://127.0.0.1:1/0"


@pytest.fixture
def rq():
    jobs = RqRegistry()
    yield jobs
    jobs.cleanup()


async def _claim(label: str, state_version: int = 4) -> tuple[uuid.UUID, str]:
    """A running claim of a fresh tender, as the job takes it. Returns the
    claim's id and its job's id."""
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    knowledge_item_id = uuid.uuid4()
    token = await reanalysis_service.claim_reanalysis(tender.id, knowledge_item_id, state_version)
    assert token is not None
    claim_id = await reanalysis_service.claim_id(tender.id, knowledge_item_id, state_version)
    assert claim_id is not None
    return claim_id, reanalysis_service.reanalysis_job_id(tender.id, state_version)


async def _reload(claim_id: uuid.UUID) -> ReanalysisClaim:
    async with UnitOfWork() as uow:
        claim = await uow.session.get(ReanalysisClaim, claim_id)
        assert claim is not None
        return claim


async def _set(claim_id: uuid.UUID, **values) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(ReanalysisClaim).where(ReanalysisClaim.id == claim_id).values(**values)
        )
        await uow.commit()


async def _reap(rq: RqRegistry, inspector: RunJobInspector | None = None) -> dict:
    inspector = inspector or RunJobInspector(rq.connection)
    reaped = await reanalysis_service.reap_lost_reanalyses(_EVERY_CLAIM, inspector.verdict)
    return {r.claim_id: r for r in reaped}


# --- lost: RQ says the job will never finish the claim ----------------------


@pytest.mark.parametrize(
    ("where", "rule"),
    [("failed", JOB_FAILED), ("canceled", JOB_CANCELED), ("finished", JOB_FINISHED)],
)
async def test_a_job_rq_will_never_run_again_ends_its_claim_as_worker_lost(rq, where, rule):
    """R1. A finished job with its claim still running ended without
    completing it (completion is the job's last act)."""
    claim_id, job_id = await _claim(f"claim-reap-{where}")
    getattr(rq, where)(job_id)

    reaped = await _reap(rq)

    assert reaped[claim_id] == reanalysis_service.ReapedClaim(claim_id, "WORKER_LOST", rule, job_id)
    claim = await _reload(claim_id)
    assert (claim.status, claim.error_code, claim.reaped_rule) == ("failed", "WORKER_LOST", rule)
    assert claim.failed_at is not None
    assert claim_id not in await _reap(rq), "an ended claim is not reaped again"


async def test_a_job_unknown_to_rq_ends_its_claim_at_once(rq):
    """R2, the case of the claims stuck on the stack (observed: no failed
    entry and no job hash for any of them). No grace: the job inserts its
    own claim while it runs, so its hash exists while the claim is
    legitimately running."""
    claim_id, _job_id = await _claim("claim-reap-unknown")

    reaped = await _reap(rq)

    assert reaped[claim_id].rule == JOB_UNKNOWN
    assert (await _reload(claim_id)).error_code == "WORKER_LOST"


@pytest.mark.parametrize("where", ["live", "queued"])
async def test_a_job_that_may_still_run_keeps_its_claim(rq, where):
    """R3."""
    claim_id, job_id = await _claim(f"claim-reap-{where}")
    if where == "live":
        rq.started(job_id, live=True)
    else:
        rq.queued(job_id)

    assert claim_id not in await _reap(rq)
    assert (await _reload(claim_id)).status == "running"


async def test_a_lost_worker_ends_the_claim_on_the_second_look_only(rq):
    """R4, the run reaper's lost-worker simulation: a started entry past
    due (no heartbeat). First look: suspected, left alone. Two minutes on
    with the score unchanged: abandoned. A score that moved (a heartbeat
    after Redis came back) is a live worker: left alone."""
    dead, dead_job = await _claim("claim-reap-dead-worker")
    alive, alive_job = await _claim("claim-reap-alive-worker")
    rq.started(dead_job, live=False)
    rq.started(alive_job, live=False)
    clock = Clock()
    inspector = RunJobInspector(rq.connection, clock=clock)

    first = await _reap(rq, inspector)
    assert dead not in first and alive not in first

    rq.move_started_score(alive_job, by=1.0)
    clock.now += outbox_dispatcher.ABANDONED_CONFIRM_SECONDS + 1
    second = await _reap(rq, inspector)

    assert second[dead].rule == JOB_ABANDONED
    assert (await _reload(dead)).status == "failed"
    assert alive not in second
    assert (await _reload(alive)).status == "running"


# --- the lease, with Redis down ----------------------------------------------


async def test_with_redis_down_the_lease_rule_still_ends_a_lapsed_claim(caplog):
    """R5. The RQ rule fails its pass (logged, nothing written); the lease
    rule, which needs no Redis, ends the lapsed claim and leaves a live one."""
    lapsed, _ = await _claim("claim-reap-lapsed")
    live, _ = await _claim("claim-reap-lease-live")
    await _set(lapsed, locked_until=text("now() - interval '1 second'"))
    dead = Redis.from_url(_DEAD_REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)

    try:
        with caplog.at_level(logging.WARNING, logger=outbox_dispatcher.logger.name):
            await outbox_dispatcher._reap_lost_reanalyses(RunJobInspector(dead))
    finally:
        dead.close()

    assert any("reanalysis claim reaper failed" in r.getMessage() for r in caplog.records)
    claim = await _reload(lapsed)
    assert (claim.status, claim.error_code, claim.reaped_rule) == ("failed", "WORKER_LOST", "lease")
    assert (await _reload(live)).status == "running"


async def test_a_database_error_in_the_claim_reaper_is_logged_and_swallowed(monkeypatch, caplog):
    """R8. Both passes fail, both are logged, and the call returns, so the
    dispatcher's loop goes on (its tick runs this after the run reaper,
    with the same inspector: test_outbox_dispatcher.py)."""

    async def broken(*args, **kwargs):
        raise ConnectionError("fake database outage")

    monkeypatch.setattr(reanalysis_service, "reap_lost_reanalyses", broken)
    monkeypatch.setattr(reanalysis_service, "reap_lapsed_reanalyses", broken)
    connection = Redis.from_url(settings.redis_url)

    try:
        with caplog.at_level(logging.WARNING, logger=outbox_dispatcher.logger.name):
            await outbox_dispatcher._reap_lost_reanalyses(RunJobInspector(connection))
    finally:
        connection.close()

    messages = [r.getMessage() for r in caplog.records]
    assert any("reanalysis claim reaper failed" in m for m in messages)
    assert any("reanalysis claim lease reaper failed" in m for m in messages)


# --- races and bounds --------------------------------------------------------


async def test_a_claim_completed_between_the_read_and_the_write_stays_completed():
    """R6. The job completes its claim after the reaper read it running and
    before the reaper's write (here, while RQ is asked): the write names the
    status and token it read, so it matches nothing."""
    claim_id, job_id = await _claim("claim-reap-race")
    async with UnitOfWork() as uow:
        token = (
            await uow.session.execute(
                select(ReanalysisClaim.claim_token).where(ReanalysisClaim.id == claim_id)
            )
        ).scalar_one()

    async def the_job_finishes_meanwhile(asked: str) -> str | None:
        if asked == job_id:
            assert await reanalysis_service.complete_reanalysis(token)
            return JOB_FINISHED
        return None

    reaped = await reanalysis_service.reap_lost_reanalyses(_EVERY_CLAIM, the_job_finishes_meanwhile)

    assert claim_id not in {r.claim_id for r in reaped}
    claim = await _reload(claim_id)
    assert (claim.status, claim.error_code) == ("completed", None)


async def test_a_pass_takes_at_most_its_batch_oldest_first():
    """R7. Two claims older than any other (created in 2000): a batch of one
    ends the older only."""
    older, _ = await _claim("claim-reap-older")
    newer, _ = await _claim("claim-reap-newer")
    await _set(older, created_at=text("'2000-01-01'::timestamptz"))
    await _set(newer, created_at=text("'2000-01-02'::timestamptz"))

    async def lost(_job_id: str) -> str:
        return JOB_UNKNOWN

    reaped = await reanalysis_service.reap_lost_reanalyses(1, lost)

    assert [r.claim_id for r in reaped] == [older]
    assert (await _reload(newer)).status == "running"
    await reanalysis_service.reap_lost_reanalyses(_EVERY_CLAIM, lost)


async def test_the_reapers_job_id_is_the_dispatchers():
    """R9. The id the reaper asks RQ about is the id the dispatcher enqueued
    the job under."""
    tender_id = uuid.uuid4()
    event = types.SimpleNamespace(
        payload={
            "tender_id": str(tender_id),
            "knowledge_item_id": str(uuid.uuid4()),
            "state_version": 12,
        }
    )

    _queue, _job, _args, job_id = outbox_dispatcher._knowledge_selection_changed_dispatch(event)

    assert job_id == reanalysis_service.reanalysis_job_id(tender_id, 12)
    assert job_id == f"reanalyze_{tender_id}_12"
