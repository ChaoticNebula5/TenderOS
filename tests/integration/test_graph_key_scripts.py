"""scripts/check_graph_keys.py and scripts/rebuild_graph.py (phase 4 plan item 49),
through their own functions, against real Postgres and real Neo4j.

The planted nodes need the constraints gone, so the fixture drops them and puts
them back for every later test.
"""

import importlib.util
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service
from tenderos_backend.graph_projection import queries
from tenderos_backend.graph_projection.driver import ensure_constraints
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_graph_keys = _load("check_graph_keys")
rebuild_graph = _load("rebuild_graph")


async def _run(query: str) -> list[dict]:
    async with get_neo4j_session() as session:
        result = await session.run(query)
        return [dict(record) async for record in result]


@pytest_asyncio.fixture(autouse=True)
async def no_constraints():
    await _run("MATCH (n) DETACH DELETE n")
    for row in await _run("SHOW CONSTRAINTS YIELD name RETURN name"):
        await _run(f"DROP CONSTRAINT {row['name']}")
    yield
    await _run("MATCH (n) DETACH DELETE n")
    await ensure_constraints()


async def _tender_with_employee() -> tuple[uuid.UUID, uuid.UUID]:
    async with get_session() as session:
        org = Organization(name=f"Keys {uuid.uuid4().hex[:6]}")
        session.add(org)
        await session.flush()
        tender = Tender(organization_id=org.id, name="Keys Tender")
        session.add(tender)
        await session.flush()
        await session.commit()
        org_id, tender_id = org.id, tender.id
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        item, _ = await uow.knowledge.create_employee(
            organization_id=org_id, name="E", content_fingerprint=uuid.uuid4().hex, title="E"
        )
        await uow.knowledge.upsert_selection(
            tender_id=tender_id, knowledge_item_id=item.id, selected=True, actor_user_id=user.id
        )
        await uow.commit()
        return tender_id, item.id


async def _graph_shape() -> list[tuple]:
    rows = await _run(
        "MATCH (n) OPTIONAL MATCH (n)-[r]->(m) "
        "RETURN labels(n)[0] AS l, coalesce(n.id, n.knowledge_item_id) AS k, type(r) AS t, "
        "coalesce(m.id, m.knowledge_item_id) AS o ORDER BY l, k, t, o"
    )
    return [tuple(row.values()) for row in rows]


# ----------------------------------------------------------------------
# check_graph_keys.py
# ----------------------------------------------------------------------


async def test_check_says_an_empty_graph_proves_nothing(capsys):
    assert await check_graph_keys.run() == 2
    assert "EMPTY" in capsys.readouterr().out


async def test_check_passes_a_projected_graph(capsys):
    tender_id, _item = await _tender_with_employee()
    await graph_service.project_tender(tender_id)

    assert await check_graph_keys.run() == 0
    assert "clean" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("plant", "column", "expected"),
    [
        ("CREATE (:Employee {knowledge_item_id: 'x'}), (:Employee {knowledge_item_id: 'x'})", "duplicated_keys", 1),
        ("CREATE (:Capability {knowledge_item_id: 'x'}), (:Capability {knowledge_item_id: 'x'})", "duplicated_keys", 1),
        ("CREATE (:Tender {id: 'x'}), (:Tender {id: 'x'})", "duplicated_keys", 1),
        ("CREATE (:Employee {name: 'no key'})", "null_keys", 1),
        ("CREATE (:Tender {name: 'no key'}), (:Tender {name: 'no key'})", "null_keys", 2),
    ],
    ids=["shared-duplicate", "last-label-duplicate", "tender-duplicate", "null-key", "two-null-keys"],
)
async def test_check_finds_what_is_wrong(capsys, plant, column, expected):
    await _run(plant)

    assert await check_graph_keys.run() == 1
    assert "NOT CLEAN" in capsys.readouterr().out
    async with get_neo4j_session() as session:
        report = await queries.graph_key_report(session)
    assert sum(getattr(c, column) for c in report.labels) == expected
    assert not report.clean


async def test_check_finds_a_node_outside_the_known_labels(capsys):
    await _run("CREATE (:SomethingNew {id: 'x'})")

    assert await check_graph_keys.run() == 1
    assert ":SomethingNew" in capsys.readouterr().out


async def test_two_null_keys_are_not_counted_as_a_duplicated_key():
    await _run("CREATE (:Employee {name: 'a'}), (:Employee {name: 'b'})")

    async with get_neo4j_session() as session:
        report = await queries.graph_key_report(session)

    employee = next(c for c in report.labels if c.label == "Employee")
    assert (employee.nodes, employee.duplicated_keys, employee.null_keys) == (2, 0, 2)


# ----------------------------------------------------------------------
# rebuild_graph.py
# ----------------------------------------------------------------------


async def test_rebuild_refuses_without_yes(capsys):
    await _run("CREATE (:Employee {knowledge_item_id: 'keep'})")

    assert await rebuild_graph.main([]) == 2

    assert "--yes" in capsys.readouterr().err
    assert (await _run("MATCH (n) RETURN count(n) AS c")) == [{"c": 1}], "nothing may be deleted"


async def test_rebuild_removes_planted_problems_and_equals_a_clean_projection(capsys):
    tender_id, item_id = await _tender_with_employee()
    await graph_service.project_tender(tender_id)
    clean = await _graph_shape()
    # A duplicate of a real shared node, a duplicated tender-scoped node, a null
    # key and a stray label: every kind the check looks for.
    await _run(f"CREATE (:Employee {{knowledge_item_id: '{item_id}'}})")
    await _run(f"CREATE (:Tender {{id: '{tender_id}', tender_id: '{tender_id}'}})")
    await _run("CREATE (:Employee {name: 'no key'}), (:SomethingNew {id: 'x'})")

    code = await rebuild_graph.rebuild()

    assert code == 0, capsys.readouterr().out
    assert await _graph_shape() == clean
    async with get_neo4j_session() as session:
        report = await queries.graph_key_report(session)
    assert report.clean
    assert len(await _run("SHOW CONSTRAINTS YIELD name RETURN name")) == 11


async def test_rebuild_applies_constraints_before_it_projects_anything(monkeypatch):
    await _tender_with_employee()
    real = rebuild_graph.graph_service.project_tender
    seen: list[int] = []

    async def _spying(tender_id):
        seen.append(len(await _run("SHOW CONSTRAINTS YIELD name RETURN name")))
        return await real(tender_id)

    monkeypatch.setattr(rebuild_graph.graph_service, "project_tender", _spying)

    assert await rebuild_graph.rebuild() == 0
    assert seen and all(count == 11 for count in seen)


async def test_rebuild_fails_if_the_keys_are_not_clean_at_the_end(monkeypatch, capsys):
    # Something a constraint can't stop: a node with no key at all.
    tender_id, _item = await _tender_with_employee()

    async def _plants_a_null_key(_tender_id):
        await _run("CREATE (:Employee {name: 'no key'})")

    monkeypatch.setattr(rebuild_graph.graph_service, "project_tender", _plants_a_null_key)

    assert await rebuild_graph.rebuild() == 1
    assert "NOT CLEAN" in capsys.readouterr().out


async def test_rebuild_reports_a_tender_that_fails(monkeypatch, capsys):
    tender_id, _item = await _tender_with_employee()

    async def _fail(_tender_id):
        raise RuntimeError("projection broke")

    monkeypatch.setattr(rebuild_graph.graph_service, "project_tender", _fail)

    assert await rebuild_graph.rebuild() == 1
    out = capsys.readouterr().out
    assert f"[FAILED] {tender_id}" in out
    assert "projected 0/" in out
