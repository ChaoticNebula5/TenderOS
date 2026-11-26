"""Plan item 95, user decision Q4: the manual red-team operation projects the
graph first when it is stale, so the pass runs its graph checks on the
current graph instead of skipping them. A fresh graph is not re-projected; a
projection that fails is logged and the pass goes on (its graph checks are
then skipped, as before); a job timeout is not swallowed.

Real Postgres and real Neo4j; run_red_team itself is replaced by a recorder,
since what matters is the graph's state when it starts.
"""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from rq.timeouts import JobTimeoutException
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service, redteam_service
from tenderos_backend.domain.services.auth_service import register
from tenderos_backend.domain.services.tender_service import create_tender
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session
from tenderos_worker.jobs import operations as operation_jobs

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _bid() -> uuid.UUID:
    registered = await register(
        email=f"{uuid.uuid4().hex}@example.com",
        password=uuid.uuid4().hex,
        organization_name="Red Team First Org",
    )
    tender = await create_tender(
        organization_id=registered.organization.id, name="Bid", created_by=registered.user.id
    )
    return tender.id


async def _make_stale(tender_id: uuid.UUID) -> None:
    await graph_service.project_tender(tender_id)
    async with UnitOfWork() as uow:
        await uow.tenders.bump_state_version(tender_id)
        await uow.commit()
    assert not (await graph_service.get_graph_status(tender_id))["fresh"]


OPERATION_ID = uuid.uuid4()
# PR 35: the job builds its LLM client for the operation's organization; the
# client is replaced below, so any id will do.
ORGANIZATION_ID = uuid.uuid4()
passed_operation_ids: list[uuid.UUID | None] = []


def _record_red_team(monkeypatch) -> list[bool]:
    """Replace run_red_team with a recorder of the graph's freshness when the
    pass starts, and of the operation id it was given (plan items 94 and 96:
    the pass is recorded as the operation's)."""
    fresh_at_start: list[bool] = []
    passed_operation_ids.clear()

    async def recorder(_client, _model, tender_id, *, operation_id=None):
        fresh_at_start.append((await graph_service.get_graph_status(tender_id))["fresh"])
        passed_operation_ids.append(operation_id)

    monkeypatch.setattr(redteam_service, "run_red_team", recorder)
    monkeypatch.setattr(operation_jobs, "_build_llm_client", lambda _organization_id: None)
    return fresh_at_start


async def test_a_red_team_run_on_a_stale_graph_projects_it_first(monkeypatch):
    tender_id = await _bid()
    await _make_stale(tender_id)
    fresh_at_start = _record_red_team(monkeypatch)
    await operation_jobs._run_redteam(
        SimpleNamespace(tender_id=tender_id, id=OPERATION_ID, organization_id=ORGANIZATION_ID)
    )
    assert fresh_at_start == [True]
    assert passed_operation_ids == [OPERATION_ID]


async def test_a_fresh_graph_is_not_projected_again(monkeypatch):
    tender_id = await _bid()
    await graph_service.project_tender(tender_id)
    projections: list[uuid.UUID] = []
    monkeypatch.setattr(graph_service, "project_tender", projections.append)
    fresh_at_start = _record_red_team(monkeypatch)
    await operation_jobs._run_redteam(
        SimpleNamespace(tender_id=tender_id, id=OPERATION_ID, organization_id=ORGANIZATION_ID)
    )
    assert projections == [] and fresh_at_start == [True]


async def test_a_failed_projection_is_logged_and_the_pass_goes_on(monkeypatch, caplog):
    tender_id = await _bid()
    await _make_stale(tender_id)

    async def failing(_tender_id):
        raise RuntimeError("graph store unavailable")

    monkeypatch.setattr(graph_service, "project_tender", failing)
    fresh_at_start = _record_red_team(monkeypatch)
    await operation_jobs._run_redteam(
        SimpleNamespace(tender_id=tender_id, id=OPERATION_ID, organization_id=ORGANIZATION_ID)
    )
    assert fresh_at_start == [False], "the pass still runs, on the stale graph"
    assert "could not project the stale graph" in caplog.text


async def test_a_job_timeout_during_the_projection_is_not_swallowed(monkeypatch):
    tender_id = await _bid()
    await _make_stale(tender_id)

    async def timing_out(_tender_id):
        raise JobTimeoutException("job timed out")

    monkeypatch.setattr(graph_service, "project_tender", timing_out)
    fresh_at_start = _record_red_team(monkeypatch)
    with pytest.raises(JobTimeoutException):
        await operation_jobs._run_redteam(
            SimpleNamespace(tender_id=tender_id, id=OPERATION_ID, organization_id=ORGANIZATION_ID)
        )
    assert fresh_at_start == []
