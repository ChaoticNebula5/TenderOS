"""Neo4j's uniqueness constraints, and the shared driver's lifecycle, against a real
server (phase 4 plan item 49).

The session's conftests apply the constraints, so each test here that needs them
absent drops them, and the fixture puts them back for every later test.
"""

import pytest
import pytest_asyncio
import tenderos_api.main as api_main
from fastapi.testclient import TestClient
from neo4j.exceptions import ConstraintError, DatabaseError
from tenderos_backend.graph_projection import apply_constraints
from tenderos_backend.graph_projection import driver as driver_module
from tenderos_backend.graph_projection.driver import (
    NODE_KEYS,
    SHARED_LABELS,
    TENDER_SCOPED_LABELS,
    constraint_name,
    ensure_constraints,
    get_session,
)

pytestmark = pytest.mark.asyncio

_FIRST_MIDDLE_LAST = (TENDER_SCOPED_LABELS[0], SHARED_LABELS[0], SHARED_LABELS[-1])


async def _run(query: str, **params) -> list[dict]:
    async with get_session() as session:
        result = await session.run(query, **params)
        return [dict(record) async for record in result]


async def _constraints() -> dict[str, tuple[str, str, str]]:
    rows = await _run(
        "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties "
        "RETURN name, type, labelsOrTypes[0] AS label, properties[0] AS property"
    )
    return {row["name"]: (row["type"], row["label"], row["property"]) for row in rows}


async def _drop_all_constraints() -> None:
    for name in await _constraints():
        await _run(f"DROP CONSTRAINT {name}")


@pytest_asyncio.fixture
async def clean_graph():
    await _run("MATCH (n) DETACH DELETE n")
    yield
    await _run("MATCH (n) DETACH DELETE n")
    await ensure_constraints()


async def test_the_session_starts_with_every_constraint_in_place(clean_graph):
    assert len(await _constraints()) == 11


async def test_ensure_constraints_creates_exactly_the_eleven_named_pairs(clean_graph):
    await _drop_all_constraints()
    assert await _constraints() == {}

    await ensure_constraints()

    assert await _constraints() == {
        "uq_tender_id": ("UNIQUENESS", "Tender", "id"),
        "uq_requirement_id": ("UNIQUENESS", "Requirement", "id"),
        "uq_evidence_id": ("UNIQUENESS", "Evidence", "id"),
        "uq_compliance_id": ("UNIQUENESS", "Compliance", "id"),
        "uq_role_id": ("UNIQUENESS", "Role", "id"),
        "uq_proposalclaim_id": ("UNIQUENESS", "ProposalClaim", "id"),
        "uq_proposalsection_id": ("UNIQUENESS", "ProposalSection", "id"),
        "uq_employee_knowledge_item_id": ("UNIQUENESS", "Employee", "knowledge_item_id"),
        "uq_certification_knowledge_item_id": ("UNIQUENESS", "Certification", "knowledge_item_id"),
        "uq_project_knowledge_item_id": ("UNIQUENESS", "Project", "knowledge_item_id"),
        "uq_capability_knowledge_item_id": ("UNIQUENESS", "Capability", "knowledge_item_id"),
    }


async def test_a_second_call_changes_nothing(clean_graph):
    before = await _constraints()

    await ensure_constraints()

    assert await _constraints() == before


async def test_the_constraints_refuse_a_second_node_with_one_key(clean_graph):
    with pytest.raises(ConstraintError):
        await _run(
            "CREATE (:Employee {knowledge_item_id: 'x'}), (:Employee {knowledge_item_id: 'x'})"
        )


@pytest.mark.parametrize("label", _FIRST_MIDDLE_LAST)
async def test_a_duplicate_planted_on_any_label_fails_and_names_it(clean_graph, label):
    # The last label matters most: its failing statement has no later statement to
    # surface the error, so this also covers an error raised at session close.
    await _drop_all_constraints()
    key = NODE_KEYS[label]
    await _run(f"CREATE (:{label} {{{key}: 'dup'}}), (:{label} {{{key}: 'dup'}})")

    with pytest.raises(DatabaseError) as raised:
        await ensure_constraints()

    assert raised.value.code == "Neo.DatabaseError.Schema.ConstraintCreationFailed"
    assert label in str(raised.value)
    assert constraint_name(label) not in await _constraints()


async def test_a_failed_call_finishes_on_the_next_try_once_the_duplicate_is_gone(clean_graph):
    await _drop_all_constraints()
    await _run(
        "CREATE (:Employee {knowledge_item_id: 'dup'}), (:Employee {knowledge_item_id: 'dup'})"
    )
    with pytest.raises(DatabaseError):
        await ensure_constraints()
    assert 0 < len(await _constraints()) < 11, "statements before the conflict have applied"

    await _run("MATCH (n:Employee) DETACH DELETE n")
    await ensure_constraints()

    assert len(await _constraints()) == 11


async def test_apply_constraints_fails_closed_on_a_duplicate(clean_graph, capsys):
    await _drop_all_constraints()
    await _run(
        "CREATE (:Capability {knowledge_item_id: 'dup'}), (:Capability {knowledge_item_id: 'dup'})"
    )

    code = await apply_constraints.run()

    assert code == 1, "a non-zero exit is what keeps the app services down"
    stderr = capsys.readouterr().err
    assert "scripts/rebuild_graph.py" in stderr
    assert "Capability" in stderr


async def test_apply_constraints_succeeds_and_closes_its_driver(clean_graph, capsys):
    await _drop_all_constraints()

    code = await apply_constraints.run()

    assert code == 0
    assert driver_module._driver is None
    assert "constraints in place" in capsys.readouterr().out
    assert len(await _constraints()) == 11


async def test_a_real_testclient_shutdown_leaves_the_shared_driver_usable(clean_graph):
    """The trap this item removes: entering `with TestClient(app)` used to close the
    shared driver for every later test. Real lifespan, real server, no patching;
    the driver was last used on this loop, and closes from TestClient's."""
    assert (await _run("RETURN 1 AS x")) == [{"x": 1}]
    used = driver_module._driver
    assert used is not None

    with TestClient(api_main.app):
        pass

    assert driver_module._driver is None
    assert (await _run("RETURN 2 AS x")) == [{"x": 2}]
    assert driver_module._driver is not used
