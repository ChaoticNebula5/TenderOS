"""Observability PR 31 (B): the metrics_exporter's role, its readings, and
the heartbeats it reads.

Against the test database (its observability views come from the models'
build, tests/models_schema.py) and the test Redis (database 1). A role is
server-wide, so each test that needs one makes a throwaway one and drops it;
queues get unique names, so nothing else in database 1 is counted.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
import pytest_asyncio
from redis import Redis
from rq import Queue, SimpleWorker, Worker
from rq.scheduler import RQScheduler
from rq.timeouts import TimerDeathPenalty
from sqlalchemy.engine import make_url
from tenderos_backend.core.config import settings
from tenderos_backend.db.models.async_operation import AsyncOperation
from tenderos_backend.db.models.audit import AuditEvent
from tenderos_backend.db.models.organization import Organization
from tenderos_backend.db.models.outbox import OutboxEvent
from tenderos_backend.db.models.reanalysis_claim import ReanalysisClaim
from tenderos_backend.db.models.tender import Tender
from tenderos_backend.db.models.workflow import ApprovalGate, WorkflowRun
from tenderos_backend.db.observability import EXPORTER, GRAFANA, SCHEMA, VIEWS
from tenderos_backend.db.observability_role import ensure_reader_role
from tenderos_backend.db.unit_of_work import UnitOfWork
from tenderos_worker import heartbeats
from tenderos_worker.metrics_exporter import (
    ExporterConfig,
    StateCollector,
    read_postgres,
    read_redis,
    redis_metrics,
)
from tenderos_worker.worker import LogContextWorkerMixin

pytestmark = pytest.mark.asyncio

_DSN = settings.postgres_dsn.replace("+asyncpg", "")
_URL = make_url(settings.postgres_dsn)


class _InProcessWorker(LogContextWorkerMixin, SimpleWorker):
    death_penalty_class = TimerDeathPenalty


@pytest_asyncio.fixture
async def exporter_role() -> AsyncIterator[tuple[str, str]]:
    role = f"tenderos_metrics_exporter_t{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        await ensure_reader_role(admin, EXPORTER, password, role)
        yield role, password
    finally:
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def _config(role: str, password: str, port: int | None = None) -> ExporterConfig:
    return ExporterConfig(
        db_password=password,
        db_host=_URL.host or "127.0.0.1",
        db_port=port if port is not None else (_URL.port or 5432),
        db_name=_URL.database or "",
        redis_url=settings.redis_url,
        port=0,
        db_role=role,
    )


async def _as_role(role: str, password: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        host=_URL.host, port=_URL.port, user=role, password=password, database=_URL.database
    )


# --- the role -------------------------------------------------------------------


async def test_the_role_reads_the_views_and_nothing_in_public(exporter_role):
    role, password = exporter_role
    # The stack's `public` (made by initdb) gives PUBLIC USAGE; the test
    # database's (dropped and made again, tests/models_schema.py) does not.
    # Granted here as on the stack, so a table read below is refused for the
    # missing table grant itself, not for the schema.
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    finally:
        await admin.close()
    conn = await _as_role(role, password)
    try:
        for name in EXPORTER.views:
            await conn.fetch(f"SELECT * FROM {SCHEMA}.{name} LIMIT 1")
        # PR 32 (answer B): not Grafana's views, which hold ids and error
        # codes the exporter has no use for.
        for name in GRAFANA.views:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM {SCHEMA}.{name} LIMIT 1")
        for table in ("workflow_runs", "outbox_events", "audit_events", "users"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM public.{table} LIMIT 1")
        assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
        assert await conn.fetchval("SHOW statement_timeout") == "5s"
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute(f"CREATE TABLE {SCHEMA}.not_allowed (x int)")
        superuser = await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = $1", role)
        assert superuser is False
    finally:
        await conn.close()


async def test_rerunning_the_setup_changes_the_password_and_keeps_the_grants(exporter_role):
    role, password = exporter_role
    new_password = uuid.uuid4().hex
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        views = await ensure_reader_role(admin, EXPORTER, new_password, role)
    finally:
        await admin.close()

    assert views == sorted(EXPORTER.views)
    with pytest.raises(asyncpg.InvalidPasswordError):
        await _as_role(role, password)
    conn = await _as_role(role, new_password)
    await conn.close()


async def test_the_setup_refuses_an_empty_password_and_an_odd_role_name():
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        with pytest.raises(ValueError, match="empty password"):
            await ensure_reader_role(admin, EXPORTER, "", "tenderos_metrics_exporter_never")
        # A quote that would end the quoted identifier; harmless even if the
        # check were gone (it is then a syntax error, not a statement).
        with pytest.raises(ValueError, match="plain role name"):
            await ensure_reader_role(admin, EXPORTER, "x", 'tenderos_metrics_exporter_x"y')
        assert not await admin.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = 'tenderos_metrics_exporter_never'"
        )
    finally:
        # Never made unless the check is broken; then removed, since a role
        # is server-wide and would outlive this test.
        if await admin.fetchval(
            "SELECT 1 FROM pg_roles WHERE rolname = 'tenderos_metrics_exporter_never'"
        ):
            await admin.execute('DROP OWNED BY "tenderos_metrics_exporter_never"')
            await admin.execute('DROP ROLE "tenderos_metrics_exporter_never"')
        await admin.close()


# --- Grafana's reader (PR 32) ---------------------------------------------------


@pytest_asyncio.fixture
async def grafana_role() -> AsyncIterator[tuple[str, str]]:
    role = f"tenderos_grafana_reader_t{uuid.uuid4().hex[:8]}"
    password = uuid.uuid4().hex
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        await ensure_reader_role(admin, GRAFANA, password, role)
        # As on the stack (see the exporter's role test): USAGE on public, so
        # a table read is refused for the missing table grant itself.
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        yield role, password
    finally:
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


async def test_grafanas_reader_reads_its_views_and_nothing_else(grafana_role):
    role, password = grafana_role
    conn = await _as_role(role, password)
    try:
        for name in GRAFANA.views:
            await conn.fetch(f"SELECT * FROM {SCHEMA}.{name} LIMIT 1")
        for name in EXPORTER.views:
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM {SCHEMA}.{name} LIMIT 1")
        for table in ("agent_traces", "workflow_runs", "audit_events", "users", "tenders"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM public.{table} LIMIT 1")
        assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
        assert await conn.fetchval("SHOW statement_timeout") == "10s"
        with pytest.raises(asyncpg.ReadOnlySQLTransactionError):
            await conn.execute(f"CREATE TABLE {SCHEMA}.not_allowed (x int)")
    finally:
        await conn.close()


async def test_a_grant_made_by_hand_does_not_survive_the_next_start(grafana_role):
    """PR 32 (answer B): every start revokes everything in the schema before
    granting the reader's own views back by name."""
    role, password = grafana_role
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        await admin.execute(f'GRANT SELECT ON {SCHEMA}.outbox_events TO "{role}"')
        conn = await _as_role(role, password)
        await conn.fetch(f"SELECT 1 FROM {SCHEMA}.outbox_events LIMIT 1")
        await conn.close()

        views = await ensure_reader_role(admin, GRAFANA, password, role)
    finally:
        await admin.close()

    assert views == sorted(GRAFANA.views)
    conn = await _as_role(role, password)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"SELECT 1 FROM {SCHEMA}.outbox_events LIMIT 1")
    finally:
        await conn.close()


async def test_no_view_exposes_a_payload_free_text_or_a_name():
    """The views expose ids, statuses, counts and times only (decision Q6 a
    with views): no payload, prompt, refs, free-text error, audit
    before/after or metadata, and no tender or organization name."""
    forbidden = {
        "payload",
        "metadata",
        "before",
        "after",
        "error_message",
        "idempotency_key",
        "input_refs",
        "output_refs",
        "tool_names",
        "prompt_version",
        "request_payload",
        "decision_payload",
        "blockers",
        "counts",
        "name",
        "email",
    }
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        rows = await admin.fetch(
            "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = $1",
            SCHEMA,
        )
    finally:
        await admin.close()
    columns = {(r["table_name"], r["column_name"]) for r in rows}
    assert {table for table, _ in columns} == {name for name, _ in VIEWS}
    assert {(t, c) for t, c in columns if c in forbidden} == set()


async def test_the_stale_graph_flag_and_the_gate_waits_read_what_they_say():
    """Two views compute rather than copy: readiness_reports.stale_graph
    (a blocker of category stale_graph_projection) and gate_waits (pending
    gates only, from requested_at)."""
    async with UnitOfWork() as uow:
        org = Organization(name="Grafana Views Org")
        uow.session.add(org)
        await uow.session.flush()
        tender = Tender(organization_id=org.id, name="Grafana Views")
        uow.session.add(tender)
        await uow.session.flush()
        tender_id = tender.id
        run_id = uuid.uuid4()
        uow.session.add(
            WorkflowRun(
                id=run_id,
                tender_id=tender_id,
                langgraph_thread_id=str(run_id),
                status="waiting_for_human",
                prompt_bundle_version="test",
                model_config_json={},
            )
        )
        await uow.session.flush()
        opened = datetime.now(UTC) - timedelta(hours=5)
        pending = ApprovalGate(
            workflow_run_id=run_id,
            tender_id=tender_id,
            gate_key="requirement_review:1",
            gate_type="requirement_review",
            status="pending",
            request_payload={},
            requested_at=opened,
        )
        decided = ApprovalGate(
            workflow_run_id=run_id,
            tender_id=tender_id,
            gate_key="compliance_review:1",
            gate_type="compliance_review",
            status="approved",
            request_payload={},
        )
        uow.session.add_all([pending, decided])
        await uow.commit()
    admin = await asyncpg.connect(dsn=_DSN)
    try:
        for version, blockers in (
            (1, '[{"category": "stale_graph_projection", "message": "x"}]'),
            (2, '[{"category": "critical_redteam_finding", "message": "y"}]'),
        ):
            await admin.execute(
                "INSERT INTO readiness_reports (id, tender_id, tender_state_version, counts, blockers, "
                "ready, generated_at, content_hash, last_generated_at) VALUES (gen_random_uuid(), $1, $2, "
                "'{}'::jsonb, $3::jsonb, false, now(), $4, now())",
                tender_id,
                version,
                blockers,
                f"hash-{version}",
            )
        flags = dict(
            await admin.fetch(
                f"SELECT tender_state_version, stale_graph FROM {SCHEMA}.readiness_reports "
                "WHERE tender_id = $1",
                tender_id,
            )
        )
        waits = await admin.fetch(
            f"SELECT gate_type, requested_at FROM {SCHEMA}.gate_waits WHERE tender_id = $1",
            tender_id,
        )
    finally:
        await admin.close()
    assert flags == {1: True, 2: False}
    assert [(w["gate_type"], w["requested_at"]) for w in waits] == [("requirement_review", opened)]


# --- what it reads from Postgres ------------------------------------------------


class _Seeded:
    """The moments each active status's age must count from (fold-in 3 of
    PR 31's review). Each candidate column gives a different answer, so a
    query reading the wrong one fails."""

    def __init__(self) -> None:
        now = datetime.now(UTC)
        # Running run A: created 2 h ago, its current job 10 min ago.
        self.run_a_created = now - timedelta(hours=2)
        self.run_a_job = now - timedelta(minutes=10)
        # Running run B: created 30 min ago, no job recorded (a run from
        # before migration 024): the reaper's coalesce falls back to
        # created_at. The oldest running age is B's: from created_at it
        # would be A's 2 h, from current_job_at without the coalesce 10 min.
        self.run_b_created = now - timedelta(minutes=30)
        # At its gate: created 3 h ago, its last job 5 min ago. Aged from
        # created_at, since no column records when it reached the gate.
        self.waiting_created = now - timedelta(hours=3)
        self.waiting_job = now - timedelta(minutes=5)
        self.queued_operation_created = now - timedelta(hours=2)
        # Running operation: created 2 h ago, claimed 20 min ago.
        self.running_operation_created = now - timedelta(hours=2)
        self.running_operation_started = now - timedelta(minutes=20)


async def _seed() -> _Seeded:
    seeded = _Seeded()
    async with UnitOfWork() as uow:
        org = Organization(name="Exporter Org")
        uow.session.add(org)
        await uow.session.flush()
        tenders = [Tender(organization_id=org.id, name=f"Exporter {i}") for i in range(4)]
        uow.session.add_all(tenders)
        await uow.session.flush()

        def run(
            tender: Tender,
            status: str,
            created: datetime | None = None,
            job_at: datetime | None = None,
        ) -> WorkflowRun:
            run_id = uuid.uuid4()
            row = WorkflowRun(
                id=run_id,
                tender_id=tender.id,
                langgraph_thread_id=str(run_id),
                status=status,
                prompt_bundle_version="test",
                model_config_json={},
                current_job_at=job_at,
            )
            if created is not None:
                row.created_at = created
            return row

        uow.session.add_all(
            [
                run(tenders[0], "running", seeded.run_a_created, seeded.run_a_job),
                run(tenders[3], "running", seeded.run_b_created),
                run(tenders[1], "waiting_for_human", seeded.waiting_created, seeded.waiting_job),
                run(tenders[0], "failed"),
                run(tenders[0], "completed"),
                run(tenders[2], "failed"),
            ]
        )
        queued = AsyncOperation(
            organization_id=org.id, tender_id=tenders[0].id, kind="redteam_run", status="queued"
        )
        queued.created_at = seeded.queued_operation_created
        running = AsyncOperation(
            organization_id=org.id,
            tender_id=tenders[2].id,
            kind="redteam_run",
            status="running",
            started_at=seeded.running_operation_started,
        )
        running.created_at = seeded.running_operation_created
        uow.session.add_all(
            [
                queued,
                running,
                AsyncOperation(
                    organization_id=org.id,
                    tender_id=tenders[1].id,
                    kind="redteam_run",
                    status="succeeded",
                ),
            ]
        )
        for event_type, status in (
            ("WorkflowRunCreated", "failed"),
            ("WorkflowRunCreated", "failed"),
            ("DocumentUploaded", "failed"),
            ("DocumentUploaded", "dispatched"),
            ("OperationRequested", "pending"),
        ):
            uow.session.add(
                OutboxEvent(
                    aggregate_type="test",
                    aggregate_id=uuid.uuid4(),
                    event_type=event_type,
                    payload={},
                    idempotency_key=uuid.uuid4().hex,
                    status=status,
                )
            )
        for action, metadata in (
            ("workflow_run_reaped", {"rule": "dead_letter"}),
            ("workflow_run_reaped", {"rule": "dead_letter"}),
            ("workflow_run_reaped", {"rule": "abandoned"}),
            ("workflow_run_timed_out", {"error_code": "job_timeout"}),
            ("gate_decided", {"rule": "never shown"}),
        ):
            uow.session.add(
                AuditEvent(
                    organization_id=org.id,
                    actor_type="system",
                    actor_id="test",
                    action=action,
                    event_metadata=metadata,
                )
            )
        # Plan item 124: claims in every state. A day either side of now, so
        # the host's clock and the database's cannot disagree on the leases.
        now = datetime.now(UTC)
        for status, lease, ended in (
            ("running", now + timedelta(days=1), {}),
            ("running", now - timedelta(days=1), {}),
            ("completed", now, {"completed_at": now}),
            ("failed", now, {"failed_at": now, "error_code": "GRAPH_STALE"}),
            ("failed", now, {"failed_at": now, "error_code": "GRAPH_STALE"}),
            (
                "failed",
                now - timedelta(days=1),
                {"failed_at": now, "error_code": "WORKER_LOST", "reaped_rule": "lease"},
            ),
        ):
            uow.session.add(
                ReanalysisClaim(
                    tender_id=tenders[0].id,
                    knowledge_item_id=uuid.uuid4(),
                    state_version=1,
                    status=status,
                    claim_token=uuid.uuid4(),
                    locked_until=lease,
                    **ended,
                )
            )
        await uow.commit()
    return seeded


async def test_the_postgres_numbers_match_sql_on_the_tables(exporter_role):
    seeded = await _seed()
    role, password = exporter_role

    state = await read_postgres(_config(role, password))

    admin = await asyncpg.connect(dsn=_DSN)
    try:
        runs = dict(await admin.fetch("SELECT status, count(*) FROM workflow_runs GROUP BY 1"))
        operations = dict(
            await admin.fetch("SELECT status, count(*) FROM async_operations GROUP BY 1")
        )
        dead = dict(
            await admin.fetch(
                "SELECT event_type, count(*) FROM outbox_events WHERE status = 'failed' GROUP BY 1"
            )
        )
        claims = dict(
            await admin.fetch("SELECT status, count(*) FROM reanalysis_claims GROUP BY 1")
        )
        claims_failed = dict(
            await admin.fetch(
                "SELECT error_code, count(*) FROM reanalysis_claims WHERE status = 'failed' "
                "GROUP BY 1"
            )
        )
        orphaned = await admin.fetchval(
            "SELECT count(*) FROM reanalysis_claims "
            "WHERE status = 'running' AND locked_until < now()"
        )
    finally:
        await admin.close()
    # Plan item 124.
    assert state.claims == claims == {"running": 2, "completed": 1, "failed": 3}
    assert state.claims_failed == claims_failed == {"GRAPH_STALE": 2, "WORKER_LOST": 1}
    assert state.claims_orphaned == orphaned == 1
    assert state.runs == runs == {"running": 2, "waiting_for_human": 1, "failed": 2, "completed": 1}
    assert state.operations == operations == {"queued": 1, "running": 1, "succeeded": 1}
    assert state.dead_letters == dead == {"WorkflowRunCreated": 2, "DocumentUploaded": 1}
    assert state.run_oldest == {
        "running": seeded.run_b_created,
        "waiting_for_human": seeded.waiting_created,
    }
    assert state.operation_oldest == {
        "queued": seeded.queued_operation_created,
        "running": seeded.running_operation_started,
    }
    assert state.reaped == {
        ("workflow_run_reaped", "dead_letter"): 2,
        ("workflow_run_reaped", "abandoned"): 1,
        ("workflow_run_timed_out", ""): 1,
    }


async def test_the_role_reads_only_the_two_system_ending_actions(exporter_role):
    """Fold-in 2 of PR 31's review: the audit view holds only the reaped and
    timed-out rows, so the exporter's role cannot read any other audit
    event's action, even by asking for every row."""
    await _seed()
    role, password = exporter_role
    conn = await _as_role(role, password)
    try:
        rows = await conn.fetch(f"SELECT * FROM {SCHEMA}.runs_ended_by_system")
    finally:
        await conn.close()
    assert {row["action"] for row in rows} == {"workflow_run_reaped", "workflow_run_timed_out"}
    assert len(rows) == 4
    assert set(rows[0].keys()) == {"action", "rule"}


async def test_a_store_that_cannot_be_read_reports_down_once_and_the_other_still_serves(
    exporter_role, caplog: pytest.LogCaptureFixture
):
    caplog.set_level(logging.INFO, logger="tenderos_worker.metrics_exporter")
    role, password = exporter_role
    collector = StateCollector(_config(role, password, port=1))

    def scrape() -> list:
        # In a thread, as prometheus_client's HTTP server runs collect(): the
        # collector starts its own event loop, which this test's loop forbids.
        return list(collector.collect())

    try:
        first = await asyncio.to_thread(scrape)
        second = await asyncio.to_thread(scrape)
        collector.config = _config(role, password)
        third = await asyncio.to_thread(scrape)
    finally:
        collector.redis.close()

    def up(families) -> dict[str, float]:
        (family,) = [f for f in families if f.name == "tenderos_exporter_source_up"]
        return {s.labels["source"]: s.value for s in family.samples}

    assert up(first) == up(second) == {"redis": 1, "postgres": 0}
    assert up(third) == {"redis": 1, "postgres": 1}
    names = {f.name for f in first}
    assert "tenderos_rq_queue_jobs" in names
    assert "tenderos_workflow_runs" not in names
    assert "tenderos_workflow_runs" in {f.name for f in third}
    messages = [r.getMessage() for r in caplog.records if r.name.endswith("metrics_exporter")]
    # The refused connection itself, not a loop or programming error.
    assert any("cannot read postgres: " in m and "RuntimeError" not in m for m in messages)
    assert sum("cannot read postgres" in m for m in messages) == 1
    assert sum("reads postgres again" in m for m in messages) == 1


# --- what it reads from Redis -----------------------------------------------------


@pytest.fixture
def connection() -> Iterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    client.close()


def test_the_redis_numbers_match_rqs_own_counts(connection: Redis):
    name = f"exporter-{uuid.uuid4().hex[:8]}"
    queue = Queue(name, connection=connection)
    first = queue.enqueue("builtins.print", "a")
    queue.enqueue("builtins.print", "b")
    failed = queue.enqueue("builtins.print", "c")
    queue.remove(failed)
    queue.failed_job_registry.add(failed, ttl=60)
    worker = _InProcessWorker([queue], connection=connection)
    worker.register_birth()
    try:
        worker.heartbeat()
        now = time.time()

        state = read_redis(connection, now, [name]).queues[name]

        assert state.jobs == queue.count == 2
        assert state.failed == queue.failed_job_registry.get_job_count(cleanup=False) == 1
        assert state.workers == Worker.count(connection=connection, queue=queue) == 1
        assert state.oldest_enqueued_at is not None
        assert first.enqueued_at is not None
        assert abs(state.oldest_enqueued_at.timestamp() - first.enqueued_at.timestamp()) < 0.001
        assert state.heartbeat_age is not None and state.heartbeat_age < 5

        # A worker that died without deregistering stays in RQ's set until RQ
        # cleans it; its own key has expired. RQ's count includes it; the
        # exporter counts only workers whose key still exists.
        connection.sadd(f"rq:workers:{name}", f"rq:worker:dead-{name}")
        stale = read_redis(connection, time.time(), [name]).queues[name]
        assert Worker.count(connection=connection, queue=queue) == 2
        assert stale.workers == 1
    finally:
        connection.srem(f"rq:workers:{name}", f"rq:worker:dead-{name}")
        worker.register_death()
        connection.delete(heartbeats.queue_heartbeat_key(name), queue.key)
        queue.failed_job_registry.remove(failed, delete_job=True)
        queue.delete(delete_jobs=True)


def test_a_heartbeat_ages_while_nothing_renews_it(connection: Redis):
    name = f"exporter-{uuid.uuid4().hex[:8]}"
    stamp = 1_800_000_000.0
    heartbeats.write_worker_heartbeat(connection, [name], clock=lambda: stamp)
    heartbeats.write_dispatcher_heartbeat(connection, clock=lambda: stamp)
    try:
        at_10 = read_redis(connection, stamp + 10, [name])
        at_70 = read_redis(connection, stamp + 70, [name])

        assert at_10.queues[name].heartbeat_age == pytest.approx(10)
        assert at_70.queues[name].heartbeat_age == pytest.approx(70)
        assert at_10.dispatcher_heartbeat_age == pytest.approx(10)
        assert at_70.dispatcher_heartbeat_age == pytest.approx(70)
        # A queue nothing ever served has no heartbeat at all, not an age.
        assert (
            read_redis(connection, stamp, [f"never-{name}"]).queues[f"never-{name}"].heartbeat_age
            is None
        )
    finally:
        connection.delete(heartbeats.queue_heartbeat_key(name), heartbeats.DISPATCHER_HEARTBEAT_KEY)


def test_overdue_scheduled_jobs_and_the_scheduler_lock_read_as_rq_keeps_them(
    connection: Redis,
):
    """PR 33: a job scheduled more than 60 s ago and still in RQ's scheduled
    registry is overdue; one due 30 s ago, or not yet due, is not. Promoting
    it (one pass of RQ's real scheduler, the one a worker runs) clears it.
    The lock reads as RQ's scheduler holds and releases it."""
    name = f"exporter-{uuid.uuid4().hex[:8]}"
    queue = Queue(name, connection=connection)
    now = time.time()
    for offset in (-120, -30, 300):
        queue.enqueue_at(datetime.fromtimestamp(now + offset, UTC), "builtins.print", offset)
    scheduler = RQScheduler([name], connection=connection)
    try:
        before = read_redis(connection, now, [name]).queues[name]
        assert (before.scheduled_overdue, before.scheduler_lock_held) == (1, False)

        scheduler.acquire_locks()
        held = read_redis(connection, now, [name]).queues[name]
        assert held.scheduler_lock_held is True
        families = {f.name: f for f in redis_metrics(read_redis(connection, now, [name]), now)}
        assert [s.value for s in families["tenderos_rq_scheduled_jobs_overdue"].samples] == [1]
        assert [s.value for s in families["tenderos_rq_scheduler_lock_held"].samples] == [1]

        scheduler.enqueue_scheduled_jobs()
        scheduler.release_locks()
        after = read_redis(connection, time.time(), [name]).queues[name]
        assert (after.scheduled_overdue, after.scheduler_lock_held) == (0, False)
        assert queue.count == 2  # the two due jobs promoted; the future one waits
    finally:
        scheduler.release_locks()
        queue.delete(delete_jobs=True)
        connection.delete(f"rq:scheduled:{name}")
