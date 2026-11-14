from dataclasses import dataclass

from tenderos_backend.agents.llm.client import (
    MalformedOutputError,
    ModelNotFoundError,
    RateLimitedError,
    TransientProviderError,
)

__all__ = ["RetryDecision", "classify_task_failure"]

# Deliberately coarser than gemini_adapter.py's own _BASE_BACKOFF_SECONDS
# (5.0) / _MAX_TRANSIENT_RETRIES (3) - by the time a TransientProviderError
# or an un-provider-timed RateLimitedError reaches this function, that
# call-level budget (5+10+20=35s of in-call waiting) is already spent, so a
# task-level retry waiting the SAME short intervals would just repeat a
# window that already failed. 30s base, doubling, capped at 120s: long
# enough to plausibly outlast a provider blip or a per-minute quota window
# without either constant.
_TASK_LEVEL_BASE_BACKOFF_SECONDS = 30.0
_TASK_LEVEL_MAX_BACKOFF_SECONDS = 120.0

# Unknown exceptions get one fixed, modest delay rather than exponential
# backoff - there's no evidence the failure is transient at all, so a
# growing wait doesn't buy anything a single retry doesn't; max_attempts's
# own default of 2 already bounds a deterministic bug to exactly one
# wasted retry.
_UNKNOWN_EXCEPTION_DELAY_SECONDS = 15.0

# Phase 4 plan item 19 (decision 5): the provider's own retry-after is
# honoured up to 15 minutes, never longer. Uncapped, a provider asking for
# hours would leave the run waiting on a retry nothing bounds but the run
# reaper's 6-hour cap (run_reaper.MAX_RUN_JOB_AGE), which would then fail it
# as lost. At 15 minutes the retry fires; if the provider is still limiting,
# the run's retry budget decides, as for any other failure.
MAX_PROVIDER_RETRY_AFTER_SECONDS = 15 * 60.0


@dataclass(frozen=True)
class RetryDecision:
    """What classify_task_failure hands back to a node's except-block.

    should_retry=False is today's only behavior everywhere in this
    codebase: mark_task_failed, re-raise, the run's own failed_task_ids
    grows by one, nothing requeues. should_retry=True is new - it means
    the orchestrator should requeue the SAME task (same task_id, same
    input_refs) after delay_seconds, having first spent budget_cost
    against TenderState.retry_budget_remaining (WorkflowTask.attempt_count
    is already incremented unconditionally by mark_task_failed, regardless
    of this decision - see that method's own line, untouched by this
    file).
    """

    should_retry: bool
    delay_seconds: float = 0.0
    budget_cost: int = 1


def classify_task_failure(
    exc: Exception,
    attempt_count: int,
    max_attempts: int,
    retry_budget_remaining: int,
) -> RetryDecision:
    """Major finding #17 (KNOWN_LIMITATIONS.md): `WorkflowTask.max_attempts`
    and `TenderState.retry_budget_remaining` both exist in the schema and
    are both seeded with real values (`max_attempts` defaults to 2 at the
    DB level; `retry_budget_remaining` is seeded to 10 in
    `apps/worker/.../jobs/workflow.py`), but nothing anywhere reads
    either one - every node's `except Exception` block goes straight to
    `mark_task_failed` + re-raise, unconditionally. This function is
    where that decision is made; nothing calls it yet (see this file's
    own KNOWN_LIMITATIONS.md entry for why wiring it into the 8 node
    files - a LangGraph topology change - is deliberately a separate,
    later pass from writing the decision itself).

    Two live trigger events this session, not hypothetical:
    1. A real 45.1-minute live E2E run hit a real `RateLimitedError`
       (Gemini free-tier `GenerateRequestsPerMinutePerProjectPerModel-
       FreeTier`, confirmed from the worker's own traceback) in
       `commercial_analysis_node`. That error had ALREADY exhausted I4's
       own per-call retry budget one layer down - `gemini_adapter.py`'s
       `_call_with_retry` retries 429s and 5xx errors with real
       exponential backoff BEFORE ever raising `RateLimitedError`/
       `TransientProviderError` up to the node (confirmed by reading
       `_call_with_retry` directly, not assumed - `_MAX_TRANSIENT_
       RETRIES` attempts, `_BASE_BACKOFF_SECONDS * (2 ** attempt)`).
       So the gap this function fills is NOT "retry a flaky LLM call" -
       I4 already does that, correctly, and it's tested
       (`tests/unit/test_call_with_repair.py`). The gap is one layer up:
       once a whole TASK's execution fails even after the call-level
       retries gave up, should the ORCHESTRATOR re-run the whole task,
       and under what conditions.
    2. The same live run showed `compliance_analysis`/`talent_analysis` -
       the other two branches LangGraph's `Send()` dispatches in
       parallel alongside `commercial_analysis` - sitting at
       `workflow_tasks.status='pending'`, never `started_at`. RESOLVED,
       post-B16, confirmed directly rather than left as an open question:
       LangGraph's own `_should_stop_others` (`pregel/_runner.py`) is by
       design - "if any task failed, cancel all other tasks" - and a real
       `FakeLLMClient`-scripted repro through the compiled graph
       (`tests/integration/test_graph_execution.py::
       test_one_failing_specialist_branch_cancels_its_still_running_
       siblings`) confirmed the still-in-flight siblings get
       asyncio-cancelled, never reach `mark_task_completed` OR
       `mark_task_failed` (CancelledError bypasses `except Exception`),
       and stay `'pending'` - indistinguishable from "never scheduled"
       because this codebase then wrote no intermediate "running" status.
       (Phase 4 plan item 77 added it: a task writes "running" as it
       starts; a retry puts a cancelled sibling back to "pending", and a
       run that ends leaves its unfinished tasks "skipped".) The retry
       mechanism THIS function feeds resolves
       it in practice: resuming the same checkpoint via
       `retry_workflow_run` re-executes the WHOLE aborted super-step,
       cancelled siblings included, not only the task that raised - all
       three branches reach `'completed'` on retry in the real repro.
       See KNOWN_LIMITATIONS.md's major finding #17 entry for the full
       record, including a second, more severe finding this repro
       surfaced (a cancelled sibling's broken DB connection could poison
       the pool for an unrelated later query - fixed via
       `pool_pre_ping=True`, `db/session.py`).

    Decision made, per exception type:
    - `RateLimitedError`: retryable. Honors the provider's own
      `retry_after_seconds`, up to MAX_PROVIDER_RETRY_AFTER_SECONDS (plan
      item 19), when Gemini supplied one (the most reliable
      signal available - it's the provider's own stated quota-reset
      window); falls back to this module's own task-level backoff when
      it didn't. Retrying here is still worth it even though the call
      layer already tried: a per-minute quota window that hadn't reset
      after 35s of in-call waiting plausibly has by the time a
      30-120s task-level wait elapses.
    - `TransientProviderError`: retryable, this module's own task-level
      exponential backoff. Deliberately NOT reusing gemini_adapter.py's
      own short constants - see the module-level comment on why a
      task-level retry needs to wait longer than a call-level one
      already did.
    - `MalformedOutputError`: terminal, not retried at this layer.
      `call_with_repair`'s one-repair-then-fail already ran and still
      failed. This project runs every LLM call at temperature=0
      (determinism-where-possible, established elsewhere in this
      codebase) - a bare task-level retry would re-send the identical
      prompt to a deterministic model and most likely fail identically
      again, spending budget on a retry with no real chance of success.
      Matches I4's own conservative stance ("one repair attempt, then
      fail task") rather than silently extending it a layer further.
    - `ModelNotFoundError`: terminal, always. I4 is explicit - "no
      automatic retry." A config problem, not a transient one; no
      number of attempts fixes a model ID that doesn't exist.
    - Anything else (not an `LLMError` at all - a DB error, a bug, a
      `KeyError`): retryable, once, with a fixed modest delay - not
      exponential, since nothing here confirms the failure is actually
      transient. Refusing to retry unknown exceptions would silently
      turn every genuinely transient DB hiccup into a hard failure with
      no recovery - the exact gap this function exists to close - while
      `max_attempts`'s own default of 2 already bounds a deterministic
      bug to exactly one wasted retry, not an unbounded one.

    Both ceilings - `max_attempts` (per-task) and `retry_budget_remaining`
    (whole-run-shared, so one very-flaky task can't alone exhaust every
    other task's retry budget too) - are checked first and unconditionally
    win: a task that has genuinely earned a retry by exception type still
    doesn't get one once either ceiling is hit. Every retryable outcome
    below costs exactly 1 from `retry_budget_remaining` - no failure type
    is judged expensive enough to cost more, since the per-task
    `max_attempts` ceiling already bounds any single task's own worst
    case independently of the shared budget.
    """
    if retry_budget_remaining <= 0 or attempt_count >= max_attempts:
        return RetryDecision(should_retry=False)

    if isinstance(exc, ModelNotFoundError | MalformedOutputError):
        return RetryDecision(should_retry=False)

    if isinstance(exc, RateLimitedError):
        if exc.retry_after_seconds:
            delay = min(exc.retry_after_seconds, MAX_PROVIDER_RETRY_AFTER_SECONDS)
        else:
            delay = _task_level_backoff(attempt_count)
        return RetryDecision(should_retry=True, delay_seconds=delay, budget_cost=1)

    if isinstance(exc, TransientProviderError):
        return RetryDecision(
            should_retry=True, delay_seconds=_task_level_backoff(attempt_count), budget_cost=1
        )

    return RetryDecision(
        should_retry=True, delay_seconds=_UNKNOWN_EXCEPTION_DELAY_SECONDS, budget_cost=1
    )


def _task_level_backoff(attempt_count: int) -> float:
    return min(
        _TASK_LEVEL_BASE_BACKOFF_SECONDS * (2**attempt_count), _TASK_LEVEL_MAX_BACKOFF_SECONDS
    )
