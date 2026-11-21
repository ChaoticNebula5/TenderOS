"""orchestration/retry.py::classify_task_failure - B16 Group 4's real
retry-semantics decision (major finding #17), previously a TODO(human)
scaffold with no implementation. Pure function, no LLM cost, no DB -
covers every exception branch plus both ceiling checks (max_attempts,
retry_budget_remaining) independently, since either one must win over
an otherwise-retryable exception type.
"""

from tenderos_backend.agents.llm.client import (
    LLMTimeoutError,
    MalformedOutputError,
    ModelNotFoundError,
    ProviderRequestRejectedError,
    RateLimitedError,
    TransientProviderError,
)
from tenderos_backend.orchestration.retry import (
    MAX_PROVIDER_RETRY_AFTER_SECONDS,
    classify_task_failure,
)


def test_model_not_found_is_always_terminal():
    decision = classify_task_failure(
        ModelNotFoundError("no such model"),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is False


def test_a_request_the_provider_rejected_is_always_terminal():
    # PR 28 review: a 4xx other than 404 and 429. Before, the SDK's own
    # ClientError took the unknown branch and was retried once.
    decision = classify_task_failure(
        ProviderRequestRejectedError(400, "INVALID_ARGUMENT"),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is False


def test_an_llm_timeout_is_retried_once_after_the_task_level_backoff_as_for_a_5xx():
    # PR 29 (Q4 a): exactly the 5xx rule. The workflow job classifies after
    # mark_task_failed has counted the failure, so the first failure arrives
    # with attempt_count 1: 30 s x 2^1 = 60 s.
    decision = classify_task_failure(
        LLMTimeoutError("no answer within the 180 s call deadline"),
        attempt_count=1,
        max_attempts=2,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is True
    assert decision.delay_seconds == 60.0
    assert decision.budget_cost == 1


def test_a_second_llm_timeout_is_not_retried():
    decision = classify_task_failure(
        LLMTimeoutError("no answer within the 180 s call deadline"),
        attempt_count=2,
        max_attempts=2,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is False


def test_an_llm_timeout_with_no_budget_left_is_not_retried():
    decision = classify_task_failure(
        LLMTimeoutError("no answer within the 180 s call deadline"),
        attempt_count=1,
        max_attempts=2,
        retry_budget_remaining=0,
    )
    assert decision.should_retry is False


def test_malformed_output_is_terminal_not_retried_a_third_time():
    decision = classify_task_failure(
        MalformedOutputError("bad schema"),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is False


def test_rate_limited_honors_the_providers_own_retry_after():
    decision = classify_task_failure(
        RateLimitedError("429", retry_after_seconds=7.5),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is True
    assert decision.delay_seconds == 7.5
    assert decision.budget_cost == 1


def test_rate_limited_without_a_provider_delay_falls_back_to_task_level_backoff():
    decision = classify_task_failure(
        RateLimitedError("429", retry_after_seconds=None),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is True
    assert decision.delay_seconds == 30.0


def test_transient_provider_error_backs_off_exponentially_by_attempt_count():
    first = classify_task_failure(
        TransientProviderError("5xx"), attempt_count=0, max_attempts=5, retry_budget_remaining=10
    )
    second = classify_task_failure(
        TransientProviderError("5xx"), attempt_count=1, max_attempts=5, retry_budget_remaining=10
    )
    assert first.should_retry is True
    assert first.delay_seconds == 30.0
    assert second.delay_seconds == 60.0, "attempt 1 must wait longer than attempt 0, not the same"


def test_transient_provider_error_backoff_is_capped_not_unbounded():
    decision = classify_task_failure(
        TransientProviderError("5xx"), attempt_count=5, max_attempts=10, retry_budget_remaining=10
    )
    assert decision.delay_seconds == 120.0, "5 doublings from 30.0 would exceed the cap uncapped"


def test_unknown_exception_gets_one_conservative_retry_not_zero():
    decision = classify_task_failure(
        ValueError("something unexpected"),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is True
    assert decision.delay_seconds == 15.0


def test_max_attempts_ceiling_overrides_an_otherwise_retryable_exception():
    decision = classify_task_failure(
        TransientProviderError("5xx"), attempt_count=2, max_attempts=2, retry_budget_remaining=10
    )
    assert decision.should_retry is False


def test_retry_budget_remaining_ceiling_overrides_an_otherwise_retryable_exception():
    decision = classify_task_failure(
        RateLimitedError("429", retry_after_seconds=1.0),
        attempt_count=0,
        max_attempts=5,
        retry_budget_remaining=0,
    )
    assert decision.should_retry is False


def test_a_providers_retry_after_is_clamped_to_fifteen_minutes():
    """Plan item 19 (decision 5): a provider asking for a day gets 15
    minutes; then, if it is still limiting, the run's retry budget decides.
    Uncapped, the retry would wait past the run reaper's 6-hour cap and the
    run would be failed as lost."""
    decision = classify_task_failure(
        RateLimitedError("429", retry_after_seconds=86_400.0),
        attempt_count=1,
        max_attempts=2,
        retry_budget_remaining=10,
    )
    assert decision.should_retry is True
    assert decision.delay_seconds == MAX_PROVIDER_RETRY_AFTER_SECONDS == 900.0
