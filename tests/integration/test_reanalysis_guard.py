"""Plan item 18 (decision 0036): a duplicate delivery of one knowledge-
selection change does nothing. Plan item 124: a job that cannot finish ends
its claim failed, for good.

The job's own work (graph projection, compliance re-evaluation, findings)
is replaced by counting fakes: what is under test is whether a delivery
gets to start that work at all, and how its claim ends. A delivery that
starts it calls project_tender twice (steps 1 and 5); a refused one calls
nothing.
"""

import asyncio
import logging
import types
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from _helpers import register_org_and_tender
from sqlalchemy import select, text, update
from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim
from tenderos_backend.db.repositories.reanalysis_claims import ReanalysisClaimRepository
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, reanalysis_service, redteam_checks
from tenderos_worker.jobs import reanalysis

pytestmark = pytest.mark.asyncio

_JOB_LOGGER = "tenderos_worker.jobs.reanalysis"


async def _tender_id(label: str) -> uuid.UUID:
    email = f"{label}-{uuid.uuid4().hex[:8]}@example.com"
    _registered, tender = await register_org_and_tender(email, f"{email} org", f"{email} tender")
    return tender.id


@pytest.fixture
def projections(monkeypatch) -> list[uuid.UUID]:
    """The job's work, faked: every project_tender call is recorded."""
    calls: list[uuid.UUID] = []

    async def project_tender(tender_id: uuid.UUID) -> None:
        calls.append(tender_id)

    async def no_impact(_tender_id: uuid.UUID, _knowledge_item_id: uuid.UUID) -> list:
        return []

    async def no_contradictions(_tender_id: uuid.UUID) -> list:
        return []

    monkeypatch.setattr(graph_service, "project_tender", project_tender)
    monkeypatch.setattr(graph_service, "preview_impact", no_impact)
    monkeypatch.setattr(redteam_checks, "check_graph_contradictions", no_contradictions)
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda _organization_id: object())
    return calls


async def _claims(tender_id: uuid.UUID) -> list[ReanalysisClaim]:
    async with UnitOfWork() as uow:
        result = await uow.session.execute(
            select(ReanalysisClaim).where(ReanalysisClaim.tender_id == tender_id)
        )
        return list(result.scalars())


async def _waiting_lock_seen(timeout_s: float = 5.0) -> bool:
    """Whether some transaction is waiting on another's transaction id (a
    second insert or update queued behind the first's uncommitted row),
    polled from a separate connection."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        async with get_session() as session:
            waiting = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE NOT granted AND locktype = 'transactionid'"
                    )
                )
            ).scalar_one()
        if waiting:
            return True
        await asyncio.sleep(0.02)
    return False


def _hold_the_first_until_the_second_waits(monkeypatch, method_name: str) -> dict:
    """Wrap a repository method so the first caller to get past its
    statement keeps its transaction open until the second caller is seen
    waiting on it in pg_locks. That makes the two statements overlap inside
    Postgres every time, rather than only when the scheduler happens to
    interleave them; `seen["wait"]` records that the overlap happened."""
    original = getattr(ReanalysisClaimRepository, method_name)
    seen: dict = {"first": False, "wait": False}

    async def held(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if not seen["first"]:
            seen["first"] = True
            seen["wait"] = await _waiting_lock_seen()
        return result

    monkeypatch.setattr(ReanalysisClaimRepository, method_name, held)
    return seen


def _barrier_before_claiming(monkeypatch, parties: int = 2) -> None:
    """Both deliveries reach the claim at the same moment."""
    barrier = asyncio.Barrier(parties)
    original = reanalysis_service.claim_reanalysis

    async def claim_after_the_barrier(*args, **kwargs):
        await barrier.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(reanalysis_service, "claim_reanalysis", claim_after_the_barrier)


@pytest.mark.parametrize("attempt", range(5))
async def test_two_deliveries_at_once_one_runs_and_one_is_refused(
    monkeypatch, caplog, projections, attempt
):
    tender_id = await _tender_id(f"reanalysis-race-{attempt}")
    knowledge_item_id = uuid.uuid4()
    _barrier_before_claiming(monkeypatch)
    seen = _hold_the_first_until_the_second_waits(monkeypatch, "claim")
    caplog.set_level(logging.INFO, logger=_JOB_LOGGER)

    await asyncio.gather(
        reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 5),
        reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 5),
    )

    assert seen["wait"], "the second insert never waited on the first: no overlap was tested"
    # One delivery projected twice (steps 1 and 5); the other did nothing.
    assert projections == [tender_id, tender_id]
    skipped = [r for r in caplog.records if "already claimed" in r.getMessage()]
    assert len(skipped) == 1
    [claim] = await _claims(tender_id)
    assert claim.status == "completed"


async def test_a_redelivery_after_the_first_finished_is_refused(projections):
    tender_id = await _tender_id("reanalysis-redelivery")
    knowledge_item_id = uuid.uuid4()

    for _ in range(2):
        await reanalysis.reanalyze_knowledge_selection_change(
            str(tender_id), str(knowledge_item_id), 3
        )

    assert projections == [tender_id, tender_id]


async def test_a_different_selection_change_for_the_same_tender_still_runs(projections):
    tender_id = await _tender_id("reanalysis-different")
    knowledge_item_id = uuid.uuid4()

    await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 3)
    # The next change to the same item is a new state_version.
    await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 4)

    assert projections == [tender_id] * 4
    assert sorted(c.state_version for c in await _claims(tender_id)) == [3, 4]
    assert {c.status for c in await _claims(tender_id)} == {"completed"}


async def _lapse(tender_id: uuid.UUID) -> None:
    # The database clock, which the lease check uses: the host's can differ
    # from the Docker VM's (KNOWN_LIMITATIONS, the reaper test's clock).
    await _set_claim(tender_id, locked_until=text("now() - interval '1 second'"))


async def _set_claim(tender_id: uuid.UUID, **values) -> None:
    async with UnitOfWork() as uow:
        await uow.session.execute(
            update(ReanalysisClaim).where(ReanalysisClaim.tender_id == tender_id).values(**values)
        )
        await uow.commit()


async def test_a_running_claim_with_a_live_lease_is_not_taken_over():
    tender_id = await _tender_id("reanalysis-live")
    knowledge_item_id = uuid.uuid4()
    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1) is not None

    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1) is None


async def test_a_completed_claim_is_never_taken_over():
    tender_id = await _tender_id("reanalysis-completed")
    knowledge_item_id = uuid.uuid4()
    token = await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1)
    assert token is not None
    assert await reanalysis_service.complete_reanalysis(token)
    # Even with its lease long past.
    await _set_claim(tender_id, locked_until=text("now() - interval '1 day'"))

    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1) is None


async def test_a_lapsed_lease_is_taken_over_by_exactly_one_of_two_takers(monkeypatch):
    tender_id = await _tender_id("reanalysis-takeover")
    knowledge_item_id = uuid.uuid4()
    first = await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1)
    assert first is not None
    await _lapse(tender_id)
    _barrier_before_claiming(monkeypatch)
    seen = _hold_the_first_until_the_second_waits(monkeypatch, "take_over_expired")

    tokens = await asyncio.gather(
        reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1),
        reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1),
    )

    assert seen["wait"], "the second takeover never waited on the first: no overlap was tested"
    winners = [t for t in tokens if t is not None]
    assert len(winners) == 1 and winners[0] != first
    [claim] = await _claims(tender_id)
    assert claim.status == "running"
    assert claim.claim_token == winners[0]
    # A fresh 31-minute lease (checked loosely against the host clock).
    assert claim.locked_until > datetime.now(UTC) + timedelta(minutes=25)


async def test_only_the_current_holder_can_complete_a_taken_over_claim():
    tender_id = await _tender_id("reanalysis-stale-token")
    knowledge_item_id = uuid.uuid4()
    stale = await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1)
    await _lapse(tender_id)
    current = await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1)
    assert stale is not None and current is not None

    assert not await reanalysis_service.complete_reanalysis(stale)
    [claim] = await _claims(tender_id)
    assert claim.status == "running"

    assert await reanalysis_service.complete_reanalysis(current)
    [claim] = await _claims(tender_id)
    assert claim.status == "completed" and claim.completed_at is not None


class _Neo4jDown(Exception):
    """A projection's failure, named so its class name is the claim's code."""


@pytest.mark.parametrize("failing_call", [1, 2], ids=["first projection", "second projection"])
async def test_a_job_that_raises_fails_its_claim_and_a_later_delivery_does_nothing(
    monkeypatch, failing_call
):
    """Plan item 124 (J3; replaces the accepted limit of plan item 18, "a
    job that raises keeps its claim until the lease lapses"). The first
    projection (step 1) or the second (step 5, after the compliance work)
    raising ends the claim failed with the exception's class name, and the
    exception still propagates, so RQ files the job as failed. A failed
    claim is never taken over, lease lapsed or not."""
    tender_id = await _tender_id("reanalysis-raises")
    knowledge_item_id = uuid.uuid4()
    calls = []

    async def projection(tender: uuid.UUID) -> None:
        calls.append(tender)
        if len(calls) == failing_call:
            raise _Neo4jDown("neo4j unavailable")

    async def no_impact(_tender_id: uuid.UUID, _knowledge_item_id: uuid.UUID) -> list:
        return []

    monkeypatch.setattr(graph_service, "project_tender", projection)
    monkeypatch.setattr(graph_service, "preview_impact", no_impact)
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda _organization_id: object())
    with pytest.raises(_Neo4jDown):
        await reanalysis.reanalyze_knowledge_selection_change(
            str(tender_id), str(knowledge_item_id), 2
        )

    [claim] = await _claims(tender_id)
    assert (claim.status, claim.error_code, claim.reaped_rule) == ("failed", "_Neo4jDown", None)
    assert claim.failed_at is not None and claim.completed_at is None
    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 2) is None
    await _lapse(tender_id)
    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 2) is None


def _record_sleeps(monkeypatch) -> list[float]:
    """The job's waits between attempts, recorded instead of slept: only the
    job module's own name `asyncio` is replaced, not the asyncio module."""
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(reanalysis, "asyncio", types.SimpleNamespace(sleep=sleep))
    return sleeps


async def test_a_graph_stale_through_every_attempt_fails_the_claim_as_graph_stale(
    monkeypatch, caplog
):
    """Plan item 124 (J2, user decision Q1): five attempts, each a projection
    then a preview, with waits of 2, 5, 10 and 20 s between them. The last
    stale preview ends the claim GRAPH_STALE and the job returns without
    raising (a moving bid, not a bug), with one WARNING naming the claim's
    code. Nothing after the preview runs: no compliance call."""
    tender_id = await _tender_id("reanalysis-stale")
    knowledge_item_id = uuid.uuid4()
    projections: list[uuid.UUID] = []
    sleeps = _record_sleeps(monkeypatch)

    async def project_tender(tender: uuid.UUID) -> None:
        projections.append(tender)

    async def always_stale(tender: uuid.UUID, _knowledge_item_id: uuid.UUID) -> list:
        raise graph_service.GraphStaleError(tender, 8, 7)

    async def no_compliance_call(*_args, **_kwargs):
        raise AssertionError("no compliance evaluation may run after a stale preview")

    monkeypatch.setattr(graph_service, "project_tender", project_tender)
    monkeypatch.setattr(graph_service, "preview_impact", always_stale)
    monkeypatch.setattr(reanalysis, "evaluate_compliance_for_requirement", no_compliance_call)
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda _organization_id: object())
    caplog.set_level(logging.INFO, logger=_JOB_LOGGER)

    await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 6)

    assert projections == [tender_id] * 5
    assert sleeps == [2.0, 5.0, 10.0, 20.0]
    [claim] = await _claims(tender_id)
    assert (claim.status, claim.error_code, claim.reaped_rule) == ("failed", "GRAPH_STALE", None)
    assert claim.failed_at is not None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "reanalysis failed" in warnings[0].getMessage()
    assert "GRAPH_STALE" in warnings[0].getMessage()
    retries = [r for r in caplog.records if "projecting again" in r.getMessage()]
    assert len(retries) == 4


async def test_a_graph_stale_once_is_projected_again_and_completes(monkeypatch):
    """Plan item 124: the second attempt's fresh preview is used, and the
    claim completes. Faked here; test_reanalysis_job.py has the same with
    the real projection and freshness check."""
    tender_id = await _tender_id("reanalysis-stale-once")
    knowledge_item_id = uuid.uuid4()
    projections: list[uuid.UUID] = []
    previews: list[uuid.UUID] = []
    sleeps = _record_sleeps(monkeypatch)

    async def project_tender(tender: uuid.UUID) -> None:
        projections.append(tender)

    async def stale_once(tender: uuid.UUID, _knowledge_item_id: uuid.UUID) -> list:
        previews.append(tender)
        if len(previews) == 1:
            raise graph_service.GraphStaleError(tender, 8, 7)
        return []

    async def no_contradictions(_tender_id: uuid.UUID) -> list:
        return []

    monkeypatch.setattr(graph_service, "project_tender", project_tender)
    monkeypatch.setattr(graph_service, "preview_impact", stale_once)
    monkeypatch.setattr(redteam_checks, "check_graph_contradictions", no_contradictions)
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda _organization_id: object())

    await reanalysis.reanalyze_knowledge_selection_change(str(tender_id), str(knowledge_item_id), 6)

    # Two in step 1 (each preview after its own projection), one in step 5.
    assert projections == [tender_id] * 3
    assert len(previews) == 2
    assert sleeps == [2.0]
    [claim] = await _claims(tender_id)
    assert claim.status == "completed" and claim.error_code is None


async def test_a_claim_taken_over_is_not_failed_by_the_old_job(monkeypatch, caplog):
    """Plan item 124 (J4): the job's failure write is conditional on its
    own token. Another delivery took the claim over (a new token) before
    this job raised: the claim stays running with the new token."""
    tender_id = await _tender_id("reanalysis-old-job")
    knowledge_item_id = uuid.uuid4()
    new_token = uuid.uuid4()

    async def taken_over_then_down(tender: uuid.UUID) -> None:
        await _set_claim(tender, claim_token=new_token)
        raise _Neo4jDown("neo4j unavailable")

    monkeypatch.setattr(graph_service, "project_tender", taken_over_then_down)
    caplog.set_level(logging.WARNING, logger=_JOB_LOGGER)
    with pytest.raises(_Neo4jDown):
        await reanalysis.reanalyze_knowledge_selection_change(
            str(tender_id), str(knowledge_item_id), 3
        )

    [claim] = await _claims(tender_id)
    assert (claim.status, claim.claim_token, claim.error_code) == ("running", new_token, None)
    assert any("claim not ended" in r.getMessage() for r in caplog.records)


async def test_a_failed_claim_is_never_taken_over():
    """Plan item 124 (J5): failed is terminal, with the lease live or
    lapsed."""
    tender_id = await _tender_id("reanalysis-failed-terminal")
    knowledge_item_id = uuid.uuid4()
    token = await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1)
    assert token is not None
    assert await reanalysis_service.fail_reanalysis(token, "GRAPH_STALE")

    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1) is None
    await _lapse(tender_id)
    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 1) is None
    [claim] = await _claims(tender_id)
    assert (claim.status, claim.claim_token) == ("failed", token)


async def test_a_failure_to_end_the_claim_does_not_hide_the_jobs_own_error(monkeypatch):
    """Plan item 124 (J6): if the failure write itself fails, the job's own
    exception is the one raised; the claim stays running for the reaper."""
    tender_id = await _tender_id("reanalysis-fail-write")
    knowledge_item_id = uuid.uuid4()

    async def projection_down(_tender_id: uuid.UUID) -> None:
        raise _Neo4jDown("neo4j unavailable")

    async def failure_write_down(_token: uuid.UUID, _code: str) -> bool:
        raise ConnectionError("postgres unavailable")

    monkeypatch.setattr(graph_service, "project_tender", projection_down)
    monkeypatch.setattr(reanalysis_service, "fail_reanalysis", failure_write_down)
    with pytest.raises(_Neo4jDown):
        await reanalysis.reanalyze_knowledge_selection_change(
            str(tender_id), str(knowledge_item_id), 2
        )
    [claim] = await _claims(tender_id)
    assert claim.status == "running"
