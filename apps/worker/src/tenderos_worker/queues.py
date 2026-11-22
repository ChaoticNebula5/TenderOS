"""The RQ queue names, in a module that imports nothing.

Moved here from worker.py in observability PR 31 so the metrics_exporter can
name every queue without importing worker.py, which loads the worker's
settings (REDIS_URL and POSTGRES_DSN, from .env) and the LLM factory. The
exporter is given only its own variables, not .env. worker.py re-exports
all three names, so `from tenderos_worker.worker import OPERATIONS_QUEUE`
still works.
"""

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
