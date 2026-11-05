"""Plan item 45: an idle RQ worker must not quit when Redis answers its
blocking wait late.

In the Docker Desktop VM the monotonic clock (which drives the client's
socket timeout) ran about 2.3% fast against the wall clock Redis times a
BLPOP on, so Redis's "no job" answer arrived, by the client's clock, close
to RQ's socket timeout of dequeue wait + 10 s; past it, redis-py raised
TimeoutError and the worker quit. These tests reproduce that with a proxy
in front of the test Redis that holds the empty BLPOP reply back, through
RQ's real worker setup (Worker(...), whose _set_connection adjusts the
connection's timeout, then worker.work()), with a short worker_ttl so a
run takes seconds.
"""

import logging
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue, Worker
from tenderos_worker import worker as worker_module
from tenderos_worker.settings import settings

# A worker_ttl giving RQ's shortest dequeue wait (1 s, max(1, ttl - 15)); RQ
# then sets its own socket timeout to 11 s (dequeue + 10).
SHORT_TTL = 16
# How late the proxy delivers the empty BLPOP reply: past RQ's 11 s, well
# inside worker_socket_timeout(16) = 121 s.
LATE_BY = 12.0
# RQ blocks with BLMOVE when a worker serves one queue (Redis 6.2 or later,
# rq/queue.py dequeue_any: every worker since item 44) and with BLPOP
# otherwise. Its empty answer is RESP3's null (`_`, what redis-py 8.1.0
# speaks to this Redis, seen through this proxy) or RESP2's null bulk string
# or null array.
BLOCKING_COMMANDS = (b"BLMOVE", b"BLPOP")
EMPTY_REPLIES = (b"_\r\n", b"$-1\r\n", b"*-1\r\n")


class _LateProxy:
    """A TCP proxy to the test Redis that forwards everything at once, except
    the empty answer to a blocking wait (BLMOVE or BLPOP sent on the same
    connection), which it holds for `late_by` seconds: an idle wait's
    answer arriving late by the client's clock. Other nil replies pass."""

    def __init__(self, late_by: float) -> None:
        target = urlparse(settings.redis_url)
        self._target = (target.hostname or "127.0.0.1", target.port or 6379)
        self._db = target.path or "/0"
        self._late_by = late_by
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self.delayed = 0
        self._stop = threading.Event()
        self._sockets: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}{self._db}"

    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            server = socket.create_connection(self._target)
            self._sockets += [client, server]
            waiting = threading.Event()  # this connection sent a blocking wait
            threading.Thread(
                target=self._pipe, args=(client, server, waiting, False), daemon=True
            ).start()
            threading.Thread(
                target=self._pipe, args=(server, client, waiting, True), daemon=True
            ).start()

    def _pipe(
        self, source: socket.socket, sink: socket.socket, waiting: threading.Event, replies: bool
    ) -> None:
        try:
            while not self._stop.is_set():
                data = source.recv(65536)
                if not data:
                    break
                if not replies and any(cmd in data.upper() for cmd in BLOCKING_COMMANDS):
                    waiting.set()
                if replies and waiting.is_set() and data in EMPTY_REPLIES:
                    waiting.clear()
                    self.delayed += 1
                    if self._stop.wait(self._late_by):
                        break
                sink.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, sink):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def drop_connections(self) -> None:
        """Close every proxied connection: Redis gone."""
        for sock in self._sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        self._listener.close()
        self.drop_connections()


@pytest.fixture
def late_proxy():
    proxy = _LateProxy(LATE_BY)
    yield proxy
    proxy.close()


def _run_idle_worker(connection: Redis, caplog) -> tuple[Worker, float]:
    """RQ's real worker setup and loop on an empty queue, told to stop after
    1 s idle: Worker(...) then work(max_idle_time=1)."""
    queue = Queue(f"item45-{uuid.uuid4().hex[:8]}", connection=connection)
    worker = Worker([queue], connection=connection, worker_ttl=SHORT_TTL)
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="rq.worker"):
        worker.work(max_idle_time=1, with_scheduler=False)
    return worker, time.monotonic() - started


def test_an_idle_worker_outlasts_a_late_answer_to_its_blocking_wait(late_proxy, caplog):
    connection = worker_module.worker_connection(late_proxy.url, SHORT_TTL)

    worker, elapsed = _run_idle_worker(connection, caplog)

    assert late_proxy.delayed >= 1
    messages = [r.getMessage() for r in caplog.records]
    assert not any("Redis connection timeout, quitting" in m for m in messages), messages
    assert any("idle for 1 seconds, quitting" in m for m in messages), messages
    assert elapsed >= LATE_BY
    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] == 121


def test_rqs_own_timeout_alone_quits_on_the_same_late_answer(late_proxy, caplog):
    """The crash itself, without the fix: RQ's own dequeue + 10 s."""
    connection = Redis.from_url(late_proxy.url)

    worker, elapsed = _run_idle_worker(connection, caplog)

    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] == 11
    messages = [r.getMessage() for r in caplog.records]
    assert any("Redis connection timeout, quitting" in m for m in messages), messages
    assert elapsed < LATE_BY


def test_rqs_worker_setup_keeps_the_longer_timeout_on_a_live_socket():
    """At the production worker_ttl: RQ's Worker(...) runs _set_connection,
    and a socket it then opens has 525 s, RQ's dequeue wait (405 s) plus
    the margin, not RQ's own 415 s."""
    connection = worker_module.worker_connection(settings.redis_url)
    queue = Queue(f"item45-{uuid.uuid4().hex[:8]}", connection=connection)
    worker = Worker([queue], connection=connection, worker_ttl=worker_module.WORKER_TTL)

    assert worker.dequeue_timeout == 405
    assert worker_module.worker_socket_timeout() == worker.dequeue_timeout + 120 == 525
    assert worker.connection_timeout == 415
    assert connection.connection_pool.connection_kwargs["socket_timeout"] == 525
    live = connection.connection_pool.get_connection()
    try:
        live.connect()
        assert live._sock.gettimeout() == 525
        assert connection.ping()
    finally:
        connection.connection_pool.release(live)
        connection.close()


def test_the_worker_entry_point_builds_its_worker_on_the_longer_timeout(monkeypatch, caplog):
    """worker.main() itself: the Worker it builds (RQ's real setup, with
    work() stubbed out) sits on a connection with 525 s, at WORKER_TTL, and
    the worker logs that effective timeout after RQ's setup."""
    built: list[Worker] = []

    def record_instead_of_working(self, *args, **kwargs):
        built.append(self)
        return True

    monkeypatch.setattr(Worker, "work", record_instead_of_working)
    monkeypatch.setattr("sys.argv", ["tenderos_worker.worker", "maintenance"])

    with caplog.at_level(logging.INFO, logger=worker_module.__name__):
        worker_module.main()

    [worker] = built
    assert worker.worker_ttl == worker_module.WORKER_TTL
    assert worker.connection.connection_pool.connection_kwargs["socket_timeout"] == 525
    assert "redis socket timeout 525 s for a 405 s dequeue wait (worker_ttl 420 s)" in [
        r.getMessage() for r in caplog.records
    ]
    worker.connection.close()


def test_a_redis_that_goes_away_still_fails_the_wait_at_once(late_proxy):
    """The longer timeout must not hide a dead Redis: when Redis goes away
    (its connections dropped and new ones refused) the blocking wait ends
    with a ConnectionError within seconds, after redis-py's own reconnect
    retries, not after 525 s; RQ's dequeue loop then retries and logs."""
    connection = worker_module.worker_connection(late_proxy.url)
    assert connection.ping()
    outcome: dict = {}

    def wait() -> None:
        started = time.monotonic()
        try:
            connection.blpop([f"item45-{uuid.uuid4().hex[:8]}"], 300)
        except Exception as exc:
            outcome["exc"] = exc
        outcome["elapsed"] = time.monotonic() - started

    blocked = threading.Thread(target=wait)
    blocked.start()
    time.sleep(1.0)
    late_proxy.close()
    blocked.join(timeout=60)

    assert not blocked.is_alive()
    assert isinstance(outcome.get("exc"), RedisConnectionError)
    assert outcome["elapsed"] < 30
    connection.close()
