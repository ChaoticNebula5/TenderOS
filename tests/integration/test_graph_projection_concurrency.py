"""Projections running at once, and what protects them (phase 4 plan item 49): the
uniqueness constraints, the per-tender lock, sorted rows and the bounded retry.

Measured before this was built, with no constraints: 4 projections of one tender
differed from a single projection in 10 of 10 rounds (up to 972 nodes against 243),
and check_freshness passed all 10.

Real Postgres and real Neo4j, with self-built helpers, as test_redteam_service.py
does.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable, TransientError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.graph_projection import GraphProjectionRun
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.repositories import graph_projection as repository
from tenderos_backend.db.repositories.graph_projection import ProjectionLockTimeoutError
from tenderos_backend.db.session import get_session
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_backend.domain.services import graph_service
from tenderos_backend.graph_projection import projector
from tenderos_backend.graph_projection.driver import NODE_KEYS
from tenderos_backend.graph_projection.driver import get_session as get_neo4j_session

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _clean_neo4j():
    yield
    await _wipe()


@pytest.fixture
def no_pause(monkeypatch):
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(graph_service.asyncio, "sleep", _no_sleep)


async def _wipe() -> None:
    async with get_neo4j_session() as session:
        await session.run("MATCH (n) DETACH DELETE n")


async def _org_and_tenders(count: int) -> tuple[uuid.UUID, list[uuid.UUID]]:
    async with get_session() as session:
        org = Organization(name=f"Concurrency {uuid.uuid4().hex[:6]}")
        session.add(org)
        await session.flush()
        tenders = [Tender(organization_id=org.id, name=f"Tender {i}") for i in range(count)]
        session.add_all(tenders)
        await session.flush()
        await session.commit()
        return org.id, [t.id for t in tenders]


async def _select(tender_id: uuid.UUID, item_ids: list[uuid.UUID]) -> None:
    async with UnitOfWork() as uow:
        user = await uow.users.create(email=f"{uuid.uuid4().hex}@example.test", password_hash="x")
        for item_id in item_ids:
            await uow.knowledge.upsert_selection(
                tender_id=tender_id,
                knowledge_item_id=item_id,
                selected=True,
                actor_user_id=user.id,
            )
        await uow.commit()


async def _employees(org_id: uuid.UUID, count: int) -> list[uuid.UUID]:
    async with UnitOfWork() as uow:
        ids = []
        for i in range(count):
            item, _ = await uow.knowledge.create_employee(
                organization_id=org_id,
                name=f"Employee {i}",
                content_fingerprint=uuid.uuid4().hex,
                title=f"Employee {i}",
            )
            ids.append(item.id)
        await uow.commit()
        return ids


async def _seed_rich_tender(count: int = 6) -> uuid.UUID:
    """A tender with several rows in every statement that touches a shared node:
    every shared label, HOLDS, WORKED_ON, DEMONSTRATES, PROVIDES, NAMED_IN, FILLS,
    USED_IN, plus requirements, compliance and claims of its own."""
    org_id, [tender_id] = await _org_and_tenders(1)
    employee_ids = await _employees(org_id, count)
    async with UnitOfWork() as uow:
        certification_ids, project_ids, capability_ids = [], [], []
        for i, employee_id in enumerate(employee_ids):
            cert, _ = await uow.knowledge.create_certification(
                organization_id=org_id,
                title=f"Cert {i}",
                content_fingerprint=uuid.uuid4().hex,
                holder_employee_item_id=employee_id,
            )
            project, _ = await uow.knowledge.create_past_project(
                organization_id=org_id, title=f"Project {i}", content_fingerprint=uuid.uuid4().hex
            )
            capability, _ = await uow.knowledge.create_capability(
                organization_id=org_id, title=f"Capability {i}", content_fingerprint=uuid.uuid4().hex
            )
            certification_ids.append(cert.id)
            project_ids.append(project.id)
            capability_ids.append(capability.id)
            await uow.knowledge.create_relationship(
                organization_id=org_id,
                source_knowledge_item_id=employee_id,
                relationship_type="WORKED_ON",
                target_knowledge_item_id=project.id,
                content_fingerprint=uuid.uuid4().hex,
                verified=True,
            )
            await uow.knowledge.create_relationship(
                organization_id=org_id,
                source_knowledge_item_id=project.id,
                relationship_type="DEMONSTRATES",
                target_knowledge_item_id=capability.id,
                content_fingerprint=uuid.uuid4().hex,
                verified=True,
            )

        document = await uow.documents.create(
            side="client",
            doc_type="rfp",
            filename="req.txt",
            storage_key=f"test/{uuid.uuid4()}",
            sha256=uuid.uuid4().hex,
            mime_type="text/plain",
            file_size_bytes=10,
            tender_id=tender_id,
        )
        chunk = await uow.documents.create_chunk(
            document_id=document.id,
            chunk_index=0,
            chunk_text="req",
            normalized_text="req",
            content_hash=uuid.uuid4().hex,
        )
        section = await uow.proposal.create_proposal_section(tender_id=tender_id, section_name="Team")
        for i, employee_id in enumerate(employee_ids):
            requirement = await uow.requirements.create(
                tender_id=tender_id,
                display_code=f"REQ-{i}",
                requirement_text="Requirement.",
                normalized_text="requirement.",
                category="staffing",
                mandatory=True,
                source_chunk_id=chunk.id,
                content_fingerprint=uuid.uuid4().hex,
            )
            evidence = await uow.compliance.create_evidence_link(
                tender_id=tender_id,
                source_kind="employee",
                content_fingerprint=uuid.uuid4().hex,
                knowledge_item_id=employee_id,
                verified=True,
                validity_status="valid",
            )
            entry = await uow.compliance.create_compliance_entry(
                requirement_id=requirement.id, status="fully_compliant"
            )
            await uow.compliance.link_evidence(entry.id, evidence.id)
            role = await uow.talent.create_role_requirement(
                tender_id=tender_id, role_name=f"Role {i}", content_fingerprint=uuid.uuid4().hex
            )
            match = await uow.talent.create_role_match(
                role_requirement_id=role.id, employee_knowledge_item_id=employee_id
            )
            await uow.talent.update_role_match(match.id, human_selected=True)
            claim = await uow.proposal.create_proposal_claim(
                section_id=section.id,
                claim_text=f"Claim {i}.",
                claim_type="team",
                is_factual=True,
                grounding_status="grounded",
                content_fingerprint=uuid.uuid4().hex,
            )
            await uow.proposal.link_claim_evidence(claim.id, evidence.id)
            await uow.proposal.link_claim_employee(claim.id, employee_id)
        await uow.commit()
    await _select(tender_id, employee_ids + certification_ids + project_ids + capability_ids)
    return tender_id


async def _dump(tender_id: uuid.UUID) -> tuple[list, list]:
    """What the tender's subgraph holds, in a stable order."""
    t = str(tender_id)
    async with get_neo4j_session() as session:
        nodes = await session.run(
            "MATCH (n) WHERE n.tender_id = $t RETURN labels(n)[0] AS l, n.id AS id ORDER BY l, id",
            t=t,
        )
        node_rows = [tuple(record.values()) async for record in nodes]
        relationships = await session.run(
            "MATCH (a)-[r]->(b) WHERE a.tender_id = $t OR b.tender_id = $t "
            "RETURN coalesce(a.id, a.knowledge_item_id) AS a, type(r) AS r, "
            "coalesce(b.id, b.knowledge_item_id) AS b ORDER BY a, r, b",
            t=t,
        )
        relationship_rows = [tuple(record.values()) async for record in relationships]
    return node_rows, relationship_rows


async def _duplicated_keys() -> dict[str, int]:
    found = {}
    async with get_neo4j_session() as session:
        for label, key in NODE_KEYS.items():
            result = await session.run(
                f"MATCH (n:{label}) WITH n.{key} AS k, count(*) AS c WHERE c > 1 RETURN count(k) AS d"
            )
            record = await result.single(strict=True)
            if record["d"]:
                found[label] = record["d"]
    return found


class _HeldLock:
    """Holds a tender's projection lock from a connection of its own."""

    def __init__(self, tender_id: uuid.UUID) -> None:
        self._tender_id = tender_id
        self._engine = create_async_engine(settings.postgres_dsn)
        self._connection = None
        self._transaction = None

    async def __aenter__(self) -> "_HeldLock":
        self._connection = await self._engine.connect()
        self._transaction = await self._connection.begin()
        await self._connection.execute(
            text("SELECT pg_advisory_xact_lock(:c, hashtext(:t))"),
            {"c": repository._PROJECTION_LOCK_CLASS, "t": str(self._tender_id)},
        )
        return self

    async def release(self) -> None:
        if self._transaction is not None:
            await self._transaction.rollback()
            self._transaction = None

    async def __aexit__(self, *_exc) -> None:
        await self.release()
        await self._connection.close()
        await self._engine.dispose()


async def _runs(tender_id: uuid.UUID) -> list[GraphProjectionRun]:
    async with get_session() as session:
        result = await session.execute(
            select(GraphProjectionRun).where(GraphProjectionRun.tender_id == tender_id)
        )
        return list(result.scalars().all())


def _counting_writes(monkeypatch, outcomes: list) -> list[int]:
    """Replaces the graph write: each call takes the next outcome, raising it if it
    is an exception and returning counts otherwise."""
    calls: list[int] = []

    async def _write(_uow, _session, _tender_id):
        calls.append(1)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(graph_service, "_write_projection", _write)
    return calls


# ----------------------------------------------------------------------
# The proofs: projections at once.
# ----------------------------------------------------------------------


async def test_four_projections_of_one_tender_fail_nothing_and_equal_one_projection():
    tender_id = await _seed_rich_tender()
    await graph_service.project_tender(tender_id)
    expected = await _dump(tender_id)
    assert len(expected[0]) > 20 and len(expected[1]) > 20, "too small a graph to prove anything"

    for _ in range(5):
        await _wipe()
        results = await asyncio.gather(
            *(graph_service.project_tender(tender_id) for _ in range(4)), return_exceptions=True
        )

        failures = [r for r in results if isinstance(r, BaseException)]
        assert failures == [], "every projection must succeed; EntityNotFound here is a stop-and-report"
        assert await _dump(tender_id) == expected
        assert await _duplicated_keys() == {}
        async with get_neo4j_session() as session:
            await graph_service.check_freshness(tender_id, session)


async def test_tenders_sharing_employees_leave_one_node_per_key():
    org_id, tender_ids = await _org_and_tenders(4)
    employee_ids = await _employees(org_id, 12)
    for tender_id in tender_ids:
        await _select(tender_id, employee_ids)

    for _ in range(5):
        await _wipe()
        results = await asyncio.gather(
            *(graph_service.project_tender(t) for t in tender_ids), return_exceptions=True
        )

        assert [r for r in results if isinstance(r, BaseException)] == []
        assert await _duplicated_keys() == {}
        async with get_neo4j_session() as session:
            result = await session.run("MATCH (n:Employee) RETURN count(n) AS c")
            assert (await result.single(strict=True))["c"] == 12


# ----------------------------------------------------------------------
# The lock.
# ----------------------------------------------------------------------


async def test_a_projection_waits_for_the_lock_then_proceeds(monkeypatch):
    _org, [tender_id] = await _org_and_tenders(1)
    monkeypatch.setattr(graph_service, "_PROJECTION_LOCK_TIMEOUT_MS", 10_000)

    async with _HeldLock(tender_id) as held:
        projection = asyncio.create_task(graph_service.project_tender(tender_id))
        await asyncio.sleep(0.3)
        assert not projection.done(), "it must wait while another projection holds the lock"
        await held.release()
        run = await asyncio.wait_for(projection, 10)

    assert run.status == "completed"


async def test_the_lock_is_per_tender(monkeypatch):
    _org, [locked, other] = await _org_and_tenders(2)
    monkeypatch.setattr(graph_service, "_PROJECTION_LOCK_TIMEOUT_MS", 500)

    async with _HeldLock(locked):
        run = await asyncio.wait_for(graph_service.project_tender(other), 10)

    assert run.status == "completed"


async def test_a_lock_held_for_every_attempt_fails_the_run_after_three(monkeypatch, no_pause):
    # wait_for is here for a broken build: with no lock_timeout this would wait
    # for the holder for ever, and the holder is this test.
    _org, [tender_id] = await _org_and_tenders(1)
    monkeypatch.setattr(graph_service, "_PROJECTION_LOCK_TIMEOUT_MS", 100)
    calls = _counting_writes(monkeypatch, [(0, 0)])
    attempts = []
    real = repository.GraphProjectionRepository.lock_tender_for_projection

    async def _counting_lock(self, tender, timeout_ms):
        attempts.append(timeout_ms)
        await real(self, tender, timeout_ms)

    monkeypatch.setattr(
        repository.GraphProjectionRepository, "lock_tender_for_projection", _counting_lock
    )

    async with _HeldLock(tender_id):
        with pytest.raises(ProjectionLockTimeoutError):
            await asyncio.wait_for(graph_service.project_tender(tender_id), 10)

    assert attempts == [100, 100, 100]
    assert calls == [], "nothing may touch the graph without the lock"
    [run] = await _runs(tender_id)
    assert run.status == "failed"
    assert "held the lock" in run.error_message


async def test_a_lock_timeout_is_retried_and_can_then_succeed(monkeypatch, no_pause, caplog):
    _org, [tender_id] = await _org_and_tenders(1)
    monkeypatch.setattr(graph_service, "_PROJECTION_LOCK_TIMEOUT_MS", 100)

    async with _HeldLock(tender_id) as held:
        real = repository.GraphProjectionRepository.lock_tender_for_projection
        seen = []

        async def _release_after_first(self, tender, timeout_ms):
            seen.append(1)
            if len(seen) == 2:
                await held.release()
            await real(self, tender, timeout_ms)

        monkeypatch.setattr(
            repository.GraphProjectionRepository, "lock_tender_for_projection", _release_after_first
        )
        run = await asyncio.wait_for(graph_service.project_tender(tender_id), 10)

    assert run.status == "completed"
    assert seen == [1, 1]
    assert "ProjectionLockTimeoutError" in caplog.text


async def test_lock_timeout_lasts_only_for_the_transaction():
    # One connection held across a real commit, so a session-wide setting would
    # still show afterwards. A pooled session's next statement can land on another
    # connection and prove nothing, and a rollback would undo either kind.
    _org, [tender_id] = await _org_and_tenders(1)
    engine = create_async_engine(settings.postgres_dsn)
    try:
        async with engine.connect() as connection:
            async with AsyncSession(
                bind=connection, join_transaction_mode="create_savepoint"
            ) as session:
                repo = repository.GraphProjectionRepository(session)
                await repo.lock_tender_for_projection(tender_id, 1234)
                inside = (await session.execute(text("SHOW lock_timeout"))).scalar_one()
                await session.commit()
            await connection.commit()
            after = (await connection.execute(text("SHOW lock_timeout"))).scalar_one()
            await connection.rollback()
    finally:
        await engine.dispose()

    assert inside == "1234ms"
    assert after == "0"


async def test_the_lock_is_released_when_the_projection_ends(monkeypatch):
    _org, [tender_id] = await _org_and_tenders(1)
    monkeypatch.setattr(graph_service, "_PROJECTION_LOCK_TIMEOUT_MS", 200)

    first = await graph_service.project_tender(tender_id)
    second = await asyncio.wait_for(graph_service.project_tender(tender_id), 10)

    assert (first.status, second.status) == ("completed", "completed")


async def test_a_database_error_that_is_not_a_lock_timeout_stays_what_it_is(monkeypatch):
    class _Orig(Exception):
        sqlstate = "57014"  # query_canceled

    class _Session:
        async def execute(self, statement, _params=None):
            if "pg_advisory_xact_lock" in str(statement):
                raise DBAPIError("SELECT 1", {}, _Orig())

    repo = repository.GraphProjectionRepository(_Session())

    with pytest.raises(DBAPIError):
        await repo.lock_tender_for_projection(uuid.uuid4(), 100)


async def test_the_wait_is_bounded():
    # 0 would mean no timeout at all in Postgres. Three attempts at this must stay
    # under the shortest RQ job a projection runs in, 180 s.
    assert 0 < graph_service._PROJECTION_LOCK_TIMEOUT_MS * graph_service._PROJECTION_ATTEMPTS < 180_000


# ----------------------------------------------------------------------
# The retry: only what Neo4j calls transient, plus the lock timeout.
# ----------------------------------------------------------------------


def _deadlock() -> Neo4jError:
    # The driver's own mapping from a server code, not a hand-picked class.
    error = Neo4jError._hydrate_neo4j(
        code="Neo.TransientError.Transaction.DeadlockDetected", message="deadlock"
    )
    assert isinstance(error, TransientError)
    return error


async def test_a_transient_error_is_retried_and_then_succeeds(monkeypatch, no_pause):
    _org, [tender_id] = await _org_and_tenders(1)
    calls = _counting_writes(monkeypatch, [_deadlock(), _deadlock(), (3, 4)])

    run = await graph_service.project_tender(tender_id)

    assert len(calls) == 3
    assert run.status == "completed"
    assert (run.node_count, run.relationship_count) == (3, 4)


async def test_attempts_pause_between_them_and_not_after_the_last(monkeypatch):
    _org, [tender_id] = await _org_and_tenders(1)
    pauses: list[float] = []

    async def _sleep(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr(graph_service.asyncio, "sleep", _sleep)
    _counting_writes(monkeypatch, [_deadlock()] * 5)

    with pytest.raises(TransientError):
        await graph_service.project_tender(tender_id)

    assert len(pauses) == 2
    assert 0.1 <= pauses[0] <= 0.5
    assert 0.2 <= pauses[1] <= 1.0


async def test_a_persistent_transient_error_marks_the_run_failed_after_three(monkeypatch, no_pause):
    _org, [tender_id] = await _org_and_tenders(1)
    calls = _counting_writes(monkeypatch, [_deadlock()] * 5)

    with pytest.raises(TransientError):
        await graph_service.project_tender(tender_id)

    assert len(calls) == 3
    [run] = await _runs(tender_id)
    assert run.status == "failed"


@pytest.mark.parametrize(
    "error",
    [
        Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.EntityNotFound", message="gone"),
        Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.SyntaxError", message="bad"),
        ServiceUnavailable("Neo4j is down"),
        ValueError("anything else"),
    ],
    ids=["entity-not-found", "client-error", "outage", "other"],
)
async def test_anything_else_fails_the_run_at_once(monkeypatch, no_pause, error):
    _org, [tender_id] = await _org_and_tenders(1)
    calls = _counting_writes(monkeypatch, [error] * 5)

    with pytest.raises(type(error)):
        await graph_service.project_tender(tender_id)

    assert len(calls) == 1, "must not be retried"
    [run] = await _runs(tender_id)
    assert run.status == "failed"


async def test_entity_not_found_really_is_a_client_error_not_a_transient_one():
    error = Neo4jError._hydrate_neo4j(code="Neo.ClientError.Statement.EntityNotFound", message="x")

    assert isinstance(error, ClientError)
    assert not isinstance(error, graph_service._RETRYABLE)


# ----------------------------------------------------------------------
# Sorted rows.
# ----------------------------------------------------------------------

_SHARED_STATEMENTS = {
    ("nodes", "merge_employee_nodes"): ("knowledge_item_id",),
    ("nodes", "merge_certification_nodes"): ("knowledge_item_id",),
    ("nodes", "merge_project_nodes"): ("knowledge_item_id",),
    ("nodes", "merge_capability_nodes"): ("knowledge_item_id",),
    ("relationships", "merge_holds"): ("employee_id", "certification_id"),
    ("relationships", "merge_worked_on"): ("employee_id", "project_id"),
    ("relationships", "merge_demonstrates"): ("project_id", "capability_id"),
    ("relationships", "merge_provides"): ("knowledge_item_id", "evidence_id"),
    ("relationships", "merge_named_in"): ("employee_id", "claim_id"),
    ("relationships", "merge_fills"): ("employee_id", "role_id"),
    ("relationships", "merge_used_in"): ("knowledge_item_id",),
}


async def test_every_statement_touching_shared_nodes_writes_in_key_order(monkeypatch):
    tender_id = await _seed_rich_tender()
    seen: dict[str, list] = {}

    for (module_name, function_name), _keys in _SHARED_STATEMENTS.items():
        module = getattr(projector, module_name)
        real = getattr(module, function_name)

        def _recording(real=real, function_name=function_name):
            async def _call(session, rows):
                seen[function_name] = rows
                await real(session, rows)

            return _call

        monkeypatch.setattr(module, function_name, _recording())

    await graph_service.project_tender(tender_id)

    for (_module, function_name), keys in _SHARED_STATEMENTS.items():
        rows = seen[function_name]
        assert len(rows) >= 2, f"{function_name} needs several rows to show an order"
        pairs = [tuple(str(row[key]) for key in keys) for row in rows]
        assert pairs == sorted(pairs), f"{function_name} wrote its rows out of order"
