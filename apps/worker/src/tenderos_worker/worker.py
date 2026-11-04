import importlib
import logging
import os
import sys

from redis import Redis
from rq import Queue, Worker
from rq.defaults import DEFAULT_WORKER_TTL
from tenderos_backend.agents.llm.fake_fallbacks import install_fake_fallback_responses
from tenderos_backend.core.log_redaction import configure_logging

from tenderos_worker.settings import settings

logger = logging.getLogger(__name__)

# Queue names per Build Guide Part B §R1 (Long-Running Execution Model).
QUEUE_NAMES = ["ingestion", "workflow", "projection", "evaluation", "maintenance"]

# Phase 4 Stage 2. Async operations (red team, proposal generation,
# commercial re-validation: work a person clicked and is waiting on) have
# their own queue, served by its own worker process (the operations_worker
# compose service, `python -m tenderos_worker.worker operations`). On the
# shared "workflow" queue an operation waited behind whatever workflow job
# was running, up to its 1800 s timeout; measured on the fake stack, a
# proposal generation started 7.3 s late, after a gate's resume job.
# The default worker does not listen to it.
OPERATIONS_QUEUE = "operations"
KNOWN_QUEUE_NAMES = [*QUEUE_NAMES, OPERATIONS_QUEUE]


# Plan item 45. An idle RQ worker blocks in BLPOP for its dequeue timeout
# (worker_ttl - 15 s, rq/worker/base.py) and Redis answers that wait on its
# own wall clock. RQ gives the client socket only 10 s more
# (connection_timeout), and the socket timeout runs on the monotonic clock.
# Measured in the Docker Desktop VM, that clock ran about 2.3% fast: a
# BLPOP(405) took 414.2 s by it, 0.8 s short of the 415 s socket timeout,
# while the wall clock (and the Windows host) counted 405.1 s. Past the
# margin the socket times out first, redis-py raises TimeoutError, which
# RQ's dequeue loop does not retry, and the worker quits. 120 s of margin
# covers drift of about 30% instead of 2.5%. A dead Redis still shows: a
# reset or closed connection raises ConnectionError at once (RQ retries it
# and logs "could not connect"), and redis-py's keepalive (30 s idle, 3
# probes 5 s apart) ends a connection to a host that has gone silently.
# Only a Redis that keeps the connection but stops answering waits for
# this timeout.
WORKER_TTL = DEFAULT_WORKER_TTL
DEQUEUE_SOCKET_MARGIN_SECONDS = 120


def worker_socket_timeout(worker_ttl: int = WORKER_TTL) -> int:
    """The socket timeout for a worker's Redis connection: RQ's dequeue
    timeout for this worker_ttl (BaseWorker.dequeue_timeout) plus the
    margin. RQ only raises a connection's timeout that is below its own
    connection_timeout (BaseWorker._set_connection), so it keeps this one."""
    dequeue_timeout = max(1, worker_ttl - 15)
    return dequeue_timeout + DEQUEUE_SOCKET_MARGIN_SECONDS


def worker_connection(redis_url: str, worker_ttl: int = WORKER_TTL) -> Redis:
    """The Redis connection an RQ worker blocks on, with a socket timeout
    well past its dequeue wait (see WORKER_TTL's comment). Only the workers
    use it: the API's rate limiter and the outbox dispatcher keep their own
    short timeouts."""
    return Redis.from_url(redis_url, socket_timeout=worker_socket_timeout(worker_ttl))


def queue_names_from(argv: list[str]) -> list[str]:
    """The queues this worker process serves: the ones named on the command
    line, or every queue except the operations queue when none is named."""
    if not argv:
        return list(QUEUE_NAMES)
    unknown = [name for name in argv if name not in KNOWN_QUEUE_NAMES]
    if unknown:
        raise SystemExit(f"unknown queue name(s): {', '.join(unknown)}")
    return list(argv)


# The module each operations job runs from. RQ forks a fresh work-horse per
# job and imports the job's module there, and this one takes 7 to 8 s to
# import (measured; most of it sentence_transformers and torch, pulled in
# through commercial_service -> document_service -> ingestion). Importing it
# once in this parent, before any fork, lets every job inherit it.
#
# A process that forks while other threads run can hang in the child (a lock
# held by a thread that does not exist there). The import loads no model
# weights and opens no connections, but numpy's and scipy's bundled
# OpenBLAS each start a pool of worker threads when they load (observed: 1
# -> 31 OS threads on 16 CPUs). The operations_worker service therefore sets
# OPENBLAS_NUM_THREADS=1 and OMP_NUM_THREADS=1 (docker-compose.yml), which
# keeps the import at one thread (observed), and the worker refuses to start
# if the import adds a thread or opens a socket (preload_operations_module).
# Operations do no numeric work, so a single-threaded BLAS costs them nothing
# today.
#
# What the check guarantees is that the preload adds no thread. It does not
# make the parent fork single-threaded: once the check has passed, RQ's own
# Worker.bootstrap starts a Redis pubsub listener thread (rq 2.12.0, as in
# 2.10.0: rq/worker/base.py, subscribe -> pubsub.run_in_thread, daemon)
# that runs until teardown, so every RQ worker, this one included, forks
# its jobs with that one thread alive (observed idle: 2 OS threads in the
# parent).
# That is RQ's own design, the same in the default worker.
_OPERATIONS_JOB_MODULE = "tenderos_worker.jobs.operations"
_BLAS_THREAD_VARIABLES = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS")


def _os_thread_count() -> int | None:
    # Linux only (/proc), the platform the workers run on and the only one
    # where RQ forks; None elsewhere.
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("Threads:"):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def _open_socket_count() -> int | None:
    try:
        fds = os.listdir("/proc/self/fd")
    except OSError:
        return None
    count = 0
    for fd in fds:
        try:
            if os.readlink(f"/proc/self/fd/{fd}").startswith("socket:"):
                count += 1
        except OSError:
            continue
    return count


def preload_operations_module() -> None:
    """Import the operations job module in this (parent) process, then fail
    fast, exiting non-zero, if that left more than one OS thread or opened
    a socket: better a worker that will not start at deploy time than a rare
    hang inside a forked job. Called before the RQ Worker exists, so the one
    thread it expects is the main thread; RQ's own listener thread starts
    later (see the comment above). Linux only; elsewhere nothing is
    counted."""
    threads_before, sockets_before = _os_thread_count(), _open_socket_count()
    importlib.import_module(_OPERATIONS_JOB_MODULE)
    threads_after, sockets_after = _os_thread_count(), _open_socket_count()
    logger.info(
        "preloaded %s: OS threads %s -> %s, open sockets %s -> %s",
        _OPERATIONS_JOB_MODULE,
        threads_before,
        threads_after,
        sockets_before,
        sockets_after,
    )
    pins = ", ".join(f"{name}={os.environ.get(name, '<unset>')}" for name in _BLAS_THREAD_VARIABLES)
    if threads_after is not None and threads_after > 1:
        logger.error(
            "refusing to start: after preloading %s this process has %d OS threads, not 1; "
            "forking jobs with threads running can hang them. Set %s to 1 for this service "
            "(now: %s), or find the import that starts a thread.",
            _OPERATIONS_JOB_MODULE,
            threads_after,
            " and ".join(_BLAS_THREAD_VARIABLES),
            pins,
        )
        raise SystemExit(1)
    if sockets_before is not None and sockets_after is not None and sockets_after > sockets_before:
        logger.error(
            "refusing to start: preloading %s opened %d socket(s); a connection made before "
            "the fork would be shared by every job.",
            _OPERATIONS_JOB_MODULE,
            sockets_after - sockets_before,
        )
        raise SystemExit(1)


def main() -> None:
    configure_logging()
    # B17: a real no-op unless LLM_PROVIDER=fake - see that function's own
    # docstring.
    install_fake_fallback_responses()
    queue_names = queue_names_from(sys.argv[1:])
    if OPERATIONS_QUEUE in queue_names:
        # Before the Redis client exists, so its sockets cannot be counted.
        preload_operations_module()
    connection = worker_connection(settings.redis_url, WORKER_TTL)
    queues = [Queue(name, connection=connection) for name in queue_names]
    worker = Worker(queues, connection=connection, worker_ttl=WORKER_TTL)
    # After RQ's own setup (Worker.__init__ runs _set_connection), so this is
    # the timeout the blocking wait really gets (plan item 45).
    logger.info(
        "redis socket timeout %s s for a %s s dequeue wait (worker_ttl %s s)",
        connection.connection_pool.connection_kwargs.get("socket_timeout"),
        worker.dequeue_timeout,
        worker.worker_ttl,
    )
    # with_scheduler=True: B16 Group 1's retry mechanism (jobs/workflow.py's
    # _handle_graph_failure) schedules retries via Queue.enqueue_in, which
    # only ever lands a job in RQ's own scheduled-jobs registry - nothing
    # promotes it to a real queue for a worker to pick up unless a
    # scheduler component is running. RQ's own Worker starts one with this
    # flag (in rq 2.12.0, as in 2.10.0, a separate forked process named
    # "Scheduler", not a thread: rq/scheduler.py, ForkProcess); without it,
    # every scheduled retry would sit invisibly in Redis forever, never
    # executing. Both this worker and operations_worker run one; each takes
    # a per-queue lock (SET NX), so only one promotes a given queue's jobs.
    # In 2.10.0 the heartbeat extended each lock's expiry and stop deleted
    # each lock key without checking who held it, so after a lapse two
    # schedulers could both act on one queue, and stopping one could
    # release the other's lock. 2.12.0 refreshes and releases a lock only
    # if it still holds it (a compare-and-set script) and stops scheduling
    # a queue whose lock another scheduler took over (read in the 2.10.0
    # and 2.12.0 wheels, rq/scheduler.py).
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
