"""Plan item 120 (user decisions 2026-11-26: Q1 a, Q2 a): the newest
readiness report is the one with the highest generation, taken from a
database sequence on insert and on every reuse, never the one with the
latest clock stamp. The database clock behind last_generated_at steps back
(item 117, observed 2026-11-25: 7 steps of up to 1.43 s in 90 s), so a
report written after another could sort before it.

- Backward and forward clock steps are simulated by crafting
  last_generated_at; each test also shows the old order (last_generated_at,
  then id) would have chosen the other report.
- The persist transaction takes the tender's row lock, so one tender's
  report writes wait for each other and the last one committed is the
  latest; a bump in flight finishes first, so no report is stored under a
  version already moved past.
- The Work dashboard's stale-graph panel, run as written in work.json,
  picks each tender's newest report by generation too.

Real Postgres; readiness is evaluated on an unprojected graph, so its
blockers are whatever an empty bid has; only which report is current is
read.
"""

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import readiness_service

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STALE = [
    {
        "category": "stale_graph_projection",
        "target_type": "tender",
        "target_id": None,
        "message": "Graph projection is stale.",
    }
]

_TENDER_LOCK_WAIT = text(
    "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock' "
    "AND datname = current_database() AND query ILIKE '%tenders%'"
)


async def _tender() -> uuid.UUID:
    async with get_session() as session:
        org = Organization(name=f"Readiness Generation {uuid.uuid4().hex[:8]} Org")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Readiness Generation")
        session.add(tender)
        await session.commit()
        return tender.id


async def _upsert(
    tender_id: uuid.UUID, content: str, blockers: list | None = None
) -> tuple[uuid.UUID, int]:
    """Store `content` at the tender's version, as the persist does (its
    lock first). Returns the report's id and generation."""
    async with UnitOfWork() as uow:
        tender = await uow.tenders.get_for_update(tender_id)
        assert tender is not None
        report = await uow.readiness.upsert_report(
            tender_id, tender.state_version, content, {}, blockers or [], not blockers
        )
        await uow.commit()
        return report.id, report.generation


async def _stamp_ahead(report_id: uuid.UUID, seconds: int) -> None:
    """Craft last_generated_at `seconds` ahead of now, as a clock running
    fast would have written it."""
    async with get_session() as session:
        await session.execute(
            text(
                "UPDATE readiness_reports SET last_generated_at = "
                "now() + make_interval(secs => :seconds) WHERE id = :id"
            ),
            {"seconds": seconds, "id": report_id},
        )
        await session.commit()


async def _stamp_before(report_id: uuid.UUID, other_id: uuid.UUID, seconds: int) -> None:
    async with get_session() as session:
        await session.execute(
            text(
                "UPDATE readiness_reports SET last_generated_at = "
                "(SELECT last_generated_at FROM readiness_reports WHERE id = :other) "
                "- make_interval(secs => :seconds) WHERE id = :id"
            ),
            {"other": other_id, "seconds": seconds, "id": report_id},
        )
        await session.commit()


async def _latest(tender_id: uuid.UUID) -> uuid.UUID:
    async with UnitOfWork() as uow:
        report = await uow.readiness.get_latest_for_tender(tender_id)
    assert report is not None
    return report.id


async def _by_the_clock(tender_id: uuid.UUID) -> uuid.UUID:
    """The order before plan item 120, for the tests' own contrast."""
    async with get_session() as session:
        return (
            await session.execute(
                text(
                    "SELECT id FROM readiness_reports WHERE tender_id = :t "
                    "ORDER BY last_generated_at DESC, id DESC LIMIT 1"
                ),
                {"t": tender_id},
            )
        ).scalar_one()


async def test_a_report_written_after_another_is_latest_though_the_clock_stepped_back():
    tender_id = await _tender()
    first, first_generation = await _upsert(tender_id, "content-a")
    second, second_generation = await _upsert(tender_id, "content-b")
    # The clock stepped back between the two writes: the second report's
    # stamp is a second earlier than the first's.
    await _stamp_before(second, first, 1)

    assert second_generation > first_generation
    assert await _by_the_clock(tender_id) == first, "the old order would choose the first"
    assert await _latest(tender_id) == second


async def test_a_report_stamped_ahead_does_not_become_latest():
    tender_id = await _tender()
    first, _ = await _upsert(tender_id, "content-a")
    second, _ = await _upsert(tender_id, "content-b")
    await _stamp_ahead(first, 3600)

    assert await _by_the_clock(tender_id) == first
    assert await _latest(tender_id) == second


async def test_content_reused_after_another_is_current_again_whatever_its_stamp():
    """X, Y, X: the third write reuses X's row and gives it a new
    generation, even when the clock stamps the reuse before Y."""
    tender_id = await _tender()
    x, x_first = await _upsert(tender_id, "content-x")
    y, y_generation = await _upsert(tender_id, "content-y")
    reused, x_again = await _upsert(tender_id, "content-x")
    await _stamp_before(x, y, 1)

    assert reused == x, "the same content at the same version is one row"
    assert x_first < y_generation < x_again
    assert await _by_the_clock(tender_id) == y
    assert await _latest(tender_id) == x


async def test_one_tenders_report_writes_wait_for_each_other_and_the_last_committed_is_latest():
    """A write that started first and committed first is not latest if
    another committed after it. The first holds the tender's lock with its
    report written; the second, a full evaluate_readiness, must wait for it
    and take a larger generation."""
    tender_id = await _tender()
    async with UnitOfWork() as holder:
        tender = await holder.tenders.get_for_update(tender_id)
        assert tender is not None
        held = await holder.readiness.upsert_report(
            tender_id, tender.state_version, "content-held", {}, [], True
        )
        second = asyncio.create_task(readiness_service.evaluate_readiness(tender_id))
        await _until_waiting(second, "the second write never waited for the tender's lock")
        await holder.commit()

    result = await asyncio.wait_for(second, timeout=30)
    assert result.generation is not None and result.generation > held.generation
    assert result.report_id != held.id
    assert await _latest(tender_id) == result.report_id


async def test_a_bump_in_flight_makes_a_report_wait_and_be_stored_at_the_new_version():
    """A state-version bump holds the tender's lock when a generate reaches
    its write. Under the lock the generate sees the committed version, so it
    computes again at the new one rather than storing a report under a
    version already moved past."""
    tender_id = await _tender()
    async with UnitOfWork() as uow:
        before = (await uow.tenders.get(tender_id)).state_version
    async with UnitOfWork() as holder:
        await holder.tenders.increment_state_version(tender_id)
        generate = asyncio.create_task(readiness_service.evaluate_readiness(tender_id))
        await _until_waiting(generate, "the generate never waited for the bump")
        await holder.commit()

    result = await asyncio.wait_for(generate, timeout=30)
    assert result.tender_state_version == before + 1
    async with get_session() as session:
        stored = (
            (
                await session.execute(
                    text("SELECT tender_state_version FROM readiness_reports WHERE tender_id = :t"),
                    {"t": tender_id},
                )
            )
            .scalars()
            .all()
        )
    assert stored == [before + 1], "no report under the version the bump moved past"


async def _until_waiting(task: asyncio.Task, what: str) -> None:
    """Wait at most 10 s until a session waits on a tender's lock, with
    `task` still running."""
    for _ in range(200):
        if task.done():
            break
        async with get_session() as session:
            if (await session.execute(_TENDER_LOCK_WAIT)).scalar_one() > 0:
                return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


def _stale_panel_sql() -> str:
    dashboard = json.loads((REPO_ROOT / "observability/dashboards/work.json").read_text())
    queries = [
        target["rawSql"]
        for panel in dashboard["panels"]
        for target in panel.get("targets", [])
        if "observability.readiness_reports" in target.get("rawSql", "")
    ]
    (query,) = queries
    return query


async def _panel_count(query: str) -> int:
    async with get_session() as session:
        return (await session.execute(text(query))).scalar_one()


async def test_the_work_dashboards_stale_graph_panel_reads_each_tenders_newest_report():
    """The panel counts tenders whose newest report has a stale graph. It
    is run as written in work.json, before and after seeding (the test
    database is shared, so only the difference is this test's). One tender's
    newest report is clean but its older stale one is stamped later; another
    tender's newest report is stale. By generation the panel counts one; by
    the clock it would count both."""
    query = _stale_panel_sql()
    before = await _panel_count(query)

    clean_now = await _tender()
    stale_before, _ = await _upsert(clean_now, "stale", STALE)
    await _upsert(clean_now, "clean")
    await _stamp_ahead(stale_before, 3600)
    stale_now = await _tender()
    await _upsert(stale_now, "clean")
    await _upsert(stale_now, "stale", STALE)

    assert await _panel_count(query) - before == 1
