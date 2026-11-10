"""Plan item 18 (decision 0036): a duplicate delivery of one knowledge-
selection change does nothing.

The job's own work (graph projection, compliance re-evaluation, findings)
is replaced by counting fakes: what is under test is whether a delivery
gets to start that work at all. A delivery that starts it calls
project_tender twice (steps 1 and 5); a refused one calls nothing.
"""

import asyncio
import logging
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
    monkeypatch.setattr(reanalysis, "_build_llm_client", lambda: object())
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


async def test_a_job_that_raises_keeps_its_claim_until_the_lease_lapses(monkeypatch):
    """The accepted limit: a failed job's claim stays running, so a
    redelivery inside the lease is refused, and one after it runs."""
    tender_id = await _tender_id("reanalysis-raises")
    knowledge_item_id = uuid.uuid4()

    async def projection_down(_tender_id: uuid.UUID) -> None:
        raise RuntimeError("neo4j unavailable")

    monkeypatch.setattr(graph_service, "project_tender", projection_down)
    with pytest.raises(RuntimeError):
        await reanalysis.reanalyze_knowledge_selection_change(
            str(tender_id), str(knowledge_item_id), 2
        )
    [claim] = await _claims(tender_id)
    assert claim.status == "running"

    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 2) is None
    await _lapse(tender_id)
    assert await reanalysis_service.claim_reanalysis(tender_id, knowledge_item_id, 2) is not None
