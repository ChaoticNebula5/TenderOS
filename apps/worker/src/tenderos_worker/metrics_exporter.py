"""Observability PR 31: the metrics_exporter (decisions Q3 a and Q4 c).

Most of what an operator asks is durable state already held in Postgres and
Redis (design section 4): queue depths, heartbeats, runs and operations by
status, dead letters, reaped runs. This process reads it on each Prometheus
scrape (every 15 s, observability/prometheus/prometheus.yml) and serves it
on :9105/metrics, so no RQ work horse ever needs a registry.

It runs only in the compose `observability` profile (Q4 c), with only its
own variables, never .env:
- METRICS_EXPORTER_DB_PASSWORD: its own Postgres login
  (tenderos_metrics_exporter, set up by tenderos_backend.db.observability_role).
  Empty: it refuses to start, with one ERROR line;
- POSTGRES_DB, and METRICS_EXPORTER_POSTGRES_HOST / _PORT (postgres, 5432);
- REDIS_URL;
- METRICS_EXPORTER_PORT (9105) and LOG_FORMAT (json).

Least access, as far as each store allows:
- Postgres: its own role, which can read the `observability` schema's views
  and nothing in `public` (migration 039); every query runs in one READ ONLY,
  REPEATABLE READ transaction (one snapshot, so the numbers agree with each
  other), with statement_timeout 5 s.
- Redis has no password or ACL here (docker-compose.yml's header), so any
  client could write. This code sends only read commands (LLEN, LINDEX,
  HGET, ZCARD, SMEMBERS, EXISTS, GET), not RQ's own helpers, which can write:
  a registry's `count` runs a cleanup, and Worker.all removes dead entries.

A store that cannot be read reports tenderos_exporter_source_up{source} 0 and
none of its metrics for that scrape (no stale numbers); the other store's
metrics are still served. The first failure and the recovery are logged once
each, not every scrape.
"""

import asyncio
import logging
import os
import signal
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from prometheus_client import CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from redis import Redis
from tenderos_backend.core.log_format import LOG_FORMATS
from tenderos_backend.core.log_redaction import configure_logging, log_startup_refusal
from tenderos_backend.db.models.async_operation import ACTIVE_STATUSES as ACTIVE_OPERATION_STATUSES
from tenderos_backend.db.models.async_operation import OPERATION_STATUSES
from tenderos_backend.db.models.workflow import ACTIVE_RUN_STATUSES, RUN_STATUSES
from tenderos_backend.db.observability import EXPORTER_LOGIN_VARIABLE, EXPORTER_ROLE, SCHEMA
from tenderos_backend.domain.events.outbox_events import ALL_EVENT_TYPES
from tenderos_backend.domain.run_endings import KNOWN_RUN_ENDINGS

from tenderos_worker.heartbeats import DISPATCHER_HEARTBEAT_KEY, heartbeat_age, queue_heartbeat_key
from tenderos_worker.queues import KNOWN_QUEUE_NAMES

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9105
STATEMENT_TIMEOUT_MS = 5000
CONNECT_TIMEOUT_SECONDS = 5
REDIS_TIMEOUT_SECONDS = 2

# RQ 2.12's key layout (rq/queue.py, rq/job.py, rq/registry.py,
# rq/worker_registration.py), read directly so only read commands are sent.
_QUEUE_KEY = "rq:queue:{}"
_JOB_KEY = "rq:job:{}"
_FAILED_REGISTRY_KEY = "rq:failed:{}"
_WORKERS_BY_QUEUE_KEY = "rq:workers:{}"

_RUN_COUNTS = f"SELECT status, count(*) FROM {SCHEMA}.workflow_runs GROUP BY status"
# Each active status's age counts from when the run's current wait began,
# where the table records it (PR 31 review, fold-in 3). Queued: created_at,
# since a run is only ever created queued and nothing moves one back.
# Running: current_job_at, the run reaper's own measure
# (WorkflowRepository.list_runs_past_cap), written by the start and resume
# claims and again by each retry, so a run resumed after days at a gate is
# young again. waiting_for_human: created_at, since no column records when
# the run reached its gate; that age is the run's whole life, not its wait.
_RUN_OLDEST = (
    "SELECT status, min(CASE WHEN status = 'running' "
    "THEN coalesce(current_job_at, created_at) ELSE created_at END) "
    f"FROM {SCHEMA}.workflow_runs WHERE status = ANY($1::text[]) GROUP BY status"
)
_OPERATION_COUNTS = f"SELECT status, count(*) FROM {SCHEMA}.operations GROUP BY status"
# Queued: created_at (only ever created queued). Running: started_at, which
# the claim writes in the same UPDATE that moves it to running.
_OPERATION_OLDEST = (
    "SELECT status, min(CASE WHEN status = 'running' "
    "THEN coalesce(started_at, created_at) ELSE created_at END) "
    f"FROM {SCHEMA}.operations WHERE status = ANY($1::text[]) GROUP BY status"
)
_DEAD_LETTERS = (
    f"SELECT event_type, count(*) FROM {SCHEMA}.outbox_events "
    "WHERE status = 'failed' GROUP BY event_type"
)
# The view holds only the two actions' rows (migration 039).
_REAPED = (
    f"SELECT action, coalesce(rule, ''), count(*) FROM {SCHEMA}.runs_ended_by_system "
    "GROUP BY action, rule"
)


class ExporterRefusedError(RuntimeError):
    """The exporter cannot start; the message names variables, never values."""


@dataclass(frozen=True)
class ExporterConfig:
    db_password: str
    db_host: str
    db_port: int
    db_name: str
    redis_url: str
    port: int
    # Fixed in production; a test passes a throwaway role.
    db_role: str = EXPORTER_ROLE

    @classmethod
    def from_environ(cls, environ: dict[str, str] | None = None) -> "ExporterConfig":
        env = os.environ if environ is None else environ
        missing = [
            name
            for name in (EXPORTER_LOGIN_VARIABLE, "POSTGRES_DB", "REDIS_URL")
            if not env.get(name)
        ]
        if missing:
            raise ExporterRefusedError(
                f"Refusing to start the metrics exporter: {', '.join(missing)} empty or unset. "
                f"{EXPORTER_LOGIN_VARIABLE} is the exporter's own Postgres password (set it "
                "in .env; the migrate service then creates its role)."
            )
        return cls(
            db_password=env[EXPORTER_LOGIN_VARIABLE],
            db_host=env.get("METRICS_EXPORTER_POSTGRES_HOST", "postgres"),
            db_port=int(env.get("METRICS_EXPORTER_POSTGRES_PORT", "5432")),
            db_name=env["POSTGRES_DB"],
            redis_url=env["REDIS_URL"],
            port=int(env.get("METRICS_EXPORTER_PORT", str(DEFAULT_PORT))),
        )


@dataclass(frozen=True)
class PostgresState:
    runs: dict[str, int]
    run_oldest: dict[str, datetime]
    operations: dict[str, int]
    operation_oldest: dict[str, datetime]
    dead_letters: dict[str, int]
    reaped: dict[tuple[str, str], int]


@dataclass(frozen=True)
class QueueState:
    jobs: int
    oldest_enqueued_at: datetime | None
    failed: int
    workers: int
    heartbeat_age: float | None


@dataclass(frozen=True)
class RedisState:
    queues: dict[str, QueueState]
    dispatcher_heartbeat_age: float | None


async def read_postgres(config: ExporterConfig) -> PostgresState:
    conn = await asyncpg.connect(
        host=config.db_host,
        port=config.db_port,
        user=config.db_role,
        password=config.db_password,
        database=config.db_name,
        timeout=CONNECT_TIMEOUT_SECONDS,
        server_settings={
            "application_name": "tenderos_metrics_exporter",
            "default_transaction_read_only": "on",
            "statement_timeout": str(STATEMENT_TIMEOUT_MS),
        },
    )
    try:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            runs = dict(await conn.fetch(_RUN_COUNTS))
            run_oldest = dict(await conn.fetch(_RUN_OLDEST, list(ACTIVE_RUN_STATUSES)))
            operations = dict(await conn.fetch(_OPERATION_COUNTS))
            operation_oldest = dict(
                await conn.fetch(_OPERATION_OLDEST, list(ACTIVE_OPERATION_STATUSES))
            )
            dead_letters = dict(await conn.fetch(_DEAD_LETTERS))
            reaped = {(r[0], r[1]): r[2] for r in await conn.fetch(_REAPED)}
    finally:
        await conn.close()
    return PostgresState(runs, run_oldest, operations, operation_oldest, dead_letters, reaped)


def _enqueued_at(raw: bytes | str | None) -> datetime | None:
    """RQ writes enqueued_at as UTC text (utils.utcformat)."""
    if not raw:
        return None
    text = raw.decode() if isinstance(raw, bytes) else raw
    try:
        parsed = datetime.fromisoformat(text.rstrip("Z"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def read_redis(
    connection: Redis, now: float, queue_names: Iterable[str] = KNOWN_QUEUE_NAMES
) -> RedisState:
    queues: dict[str, QueueState] = {}
    for name in queue_names:
        head = connection.lindex(_QUEUE_KEY.format(name), 0)
        enqueued = None
        if head is not None:
            job_id = head.decode() if isinstance(head, bytes) else str(head)
            enqueued = _enqueued_at(connection.hget(_JOB_KEY.format(job_id), "enqueued_at"))
        worker_keys = connection.smembers(_WORKERS_BY_QUEUE_KEY.format(name))
        alive = sum(1 for key in worker_keys if connection.exists(key))
        queues[name] = QueueState(
            jobs=int(connection.llen(_QUEUE_KEY.format(name))),
            oldest_enqueued_at=enqueued,
            failed=int(connection.zcard(_FAILED_REGISTRY_KEY.format(name))),
            workers=alive,
            heartbeat_age=heartbeat_age(connection.get(queue_heartbeat_key(name)), now),
        )
    dispatcher = heartbeat_age(connection.get(DISPATCHER_HEARTBEAT_KEY), now)
    return RedisState(queues, dispatcher)


def _age(moment: datetime | None, now: float) -> float:
    return 0.0 if moment is None else max(0.0, now - moment.timestamp())


def postgres_metrics(state: PostgresState, now: float) -> Iterator[Metric]:
    runs = GaugeMetricFamily("tenderos_workflow_runs", "workflow runs by status", labels=["status"])
    for status in RUN_STATUSES:
        runs.add_metric([status], state.runs.get(status, 0))
    yield runs
    run_age = GaugeMetricFamily(
        "tenderos_workflow_run_oldest_age_seconds",
        "age of the oldest run in each active status (0 when none): queued from its "
        "creation, running from its current job (restarted by a resume or a retry), "
        "waiting_for_human from its creation, not from reaching its gate",
        labels=["status"],
    )
    for status in ACTIVE_RUN_STATUSES:
        run_age.add_metric([status], _age(state.run_oldest.get(status), now))
    yield run_age
    operations = GaugeMetricFamily(
        "tenderos_operations", "async operations by status", labels=["status"]
    )
    for status in OPERATION_STATUSES:
        operations.add_metric([status], state.operations.get(status, 0))
    yield operations
    operation_age = GaugeMetricFamily(
        "tenderos_operation_oldest_age_seconds",
        "age of the oldest operation in each active status (0 when none): queued from "
        "its creation, running from its claim",
        labels=["status"],
    )
    for status in ACTIVE_OPERATION_STATUSES:
        operation_age.add_metric([status], _age(state.operation_oldest.get(status), now))
    yield operation_age
    # Every known event type and every known (action, rule) pair is emitted,
    # at 0 when it has none (PR 32, changes 1 and 2): a series that first
    # appears at 1 has no earlier sample, so neither increase() nor the
    # DeadLetter rule's rise from the hour's minimum could see that first
    # event. Anything else the database holds is emitted too, so an event
    # type or rule this list does not know still shows.
    dead = GaugeMetricFamily(
        "tenderos_outbox_dead_letters",
        "outbox events that failed every attempt and wait for a manual re-drive, by event type "
        "(every known type, 0 when it has none)",
        labels=["event_type"],
    )
    for event_type in sorted(set(ALL_EVENT_TYPES) | set(state.dead_letters)):
        dead.add_metric([event_type], state.dead_letters.get(event_type, 0))
    yield dead
    reaped = CounterMetricFamily(
        "tenderos_workflow_runs_ended_by_system",
        "runs the reaper failed (workflow_run_reaped, by rule) or RQ's timeout ended "
        "(workflow_run_timed_out), from the audit trail (every known pair, 0 when it has none)",
        labels=["action", "rule"],
    )
    for action, rule in sorted(set(KNOWN_RUN_ENDINGS) | set(state.reaped)):
        reaped.add_metric([action, rule], state.reaped.get((action, rule), 0))
    yield reaped


def redis_metrics(state: RedisState, now: float) -> Iterator[Metric]:
    families: dict[str, GaugeMetricFamily] = {
        "jobs": GaugeMetricFamily("tenderos_rq_queue_jobs", "jobs waiting", labels=["queue"]),
        "oldest": GaugeMetricFamily(
            "tenderos_rq_queue_oldest_job_age_seconds",
            "age of the job at the head of the queue, from its enqueue (0 when empty)",
            labels=["queue"],
        ),
        "failed": GaugeMetricFamily(
            "tenderos_rq_failed_jobs", "jobs in RQ's failed registry", labels=["queue"]
        ),
        "workers": GaugeMetricFamily(
            "tenderos_rq_workers", "workers registered and alive", labels=["queue"]
        ),
        "heartbeat": GaugeMetricFamily(
            "tenderos_rq_heartbeat_age_seconds",
            "seconds since any worker serving the queue last heartbeated (no sample before the "
            "first one)",
            labels=["queue"],
        ),
    }
    for name, queue in state.queues.items():
        families["jobs"].add_metric([name], queue.jobs)
        families["oldest"].add_metric([name], _age(queue.oldest_enqueued_at, now))
        families["failed"].add_metric([name], queue.failed)
        families["workers"].add_metric([name], queue.workers)
        if queue.heartbeat_age is not None:
            families["heartbeat"].add_metric([name], queue.heartbeat_age)
    yield from families.values()
    dispatcher = GaugeMetricFamily(
        "tenderos_outbox_dispatcher_heartbeat_age_seconds",
        "seconds since the outbox dispatcher last finished a tick (no sample before the first)",
    )
    if state.dispatcher_heartbeat_age is not None:
        dispatcher.add_metric([], state.dispatcher_heartbeat_age)
    yield dispatcher


class StateCollector(Collector):
    """Reads both stores on every scrape."""

    def __init__(self, config: ExporterConfig, clock: Any = time.time) -> None:
        self.config = config
        self._clock = clock
        self.redis = Redis.from_url(
            config.redis_url,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
        )
        self._failing: set[str] = set()
        self._lock = threading.Lock()

    def _note(self, source: str, error: BaseException | None) -> None:
        with self._lock:
            if error is not None and source not in self._failing:
                self._failing.add(source)
                logger.warning("metrics exporter cannot read %s: %s", source, type(error).__name__)
            elif error is None and source in self._failing:
                self._failing.discard(source)
                logger.info("metrics exporter reads %s again", source)

    def collect(self) -> Iterator[Metric]:
        now = self._clock()
        up = GaugeMetricFamily(
            "tenderos_exporter_source_up",
            "1 when this scrape read the store, 0 when it could not",
            labels=["source"],
        )
        metrics: list[Metric] = []
        try:
            metrics += list(redis_metrics(read_redis(self.redis, now), now))
            up.add_metric(["redis"], 1)
            self._note("redis", None)
        except Exception as exc:  # noqa: BLE001 - any failure means "could not read"
            up.add_metric(["redis"], 0)
            self._note("redis", exc)
        try:
            state = asyncio.run(read_postgres(self.config))
            metrics += list(postgres_metrics(state, now))
            up.add_metric(["postgres"], 1)
            self._note("postgres", None)
        except Exception as exc:  # noqa: BLE001
            up.add_metric(["postgres"], 0)
            self._note("postgres", exc)
        yield up
        yield from metrics


def main() -> None:
    log_format = os.environ.get("LOG_FORMAT", "json")
    if log_format not in LOG_FORMATS:
        log_format = "json"
    configure_logging(log_format)
    try:
        config = ExporterConfig.from_environ()
    except ExporterRefusedError as exc:
        log_startup_refusal(str(exc), log_format)
        raise SystemExit(1) from exc
    registry = CollectorRegistry()
    registry.register(StateCollector(config))
    start_http_server(config.port, registry=registry)
    logger.info("metrics exporter serving :%d/metrics", config.port)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    logger.info("metrics exporter stopping")


if __name__ == "__main__":
    main()
