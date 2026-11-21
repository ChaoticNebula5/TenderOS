import time
from dataclasses import dataclass, replace
from typing import Protocol, TypedDict

from pydantic import BaseModel


@dataclass(frozen=True)
class CallMeasurement:
    """What one logical LLM call measured, attached to the LLMError when the
    call fails (PR 28, observability). A success carries the same fields on
    its LLMResult. Either way the agent's agent_traces row gets them through
    trace_measurements(), so a failed call keeps its provider, latency and
    attempts instead of writing None for all three.

    - latency_ms: the whole call's wall time, retries and backoff included.
    - attempts: the provider requests the call made (0 for a cache hit, 1 with
      no retry; I4's repair call counts too, see call_with_repair). None when
      it is not known.
    - tokens and cost: None when the provider returned no usage (a timeout, a
      rate limit, a 5xx) or the model has no price.
    - thinking_tokens (PR 29): the tokens the model spent thinking, apart
      from output_tokens (the answer). Billed at the output rate, so
      estimated_cost_usd includes them; see gemini_adapter._estimate_cost_usd.
    """

    provider: str
    latency_ms: int
    attempts: int | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    thinking_tokens: int | None = None


class LLMError(Exception):
    """Base for every LLM-adapter failure - I4's retry policy branches on the
    concrete subclass, not on string-matching a provider's own exception.

    `measurement` is set by the adapter just before the error leaves it, and
    stays None for an error raised anywhere else (a test stub, for one)."""

    measurement: CallMeasurement | None = None


class ModelNotFoundError(LLMError):
    """The configured model ID doesn't exist for this provider/account. Never
    retried (I4 has no retry branch for a config error) and never mistaken
    for an extraction-quality problem - Part P: verify model IDs before the
    real eval run, this is what fires if that step gets skipped."""


class ProviderRequestRejectedError(LLMError):
    """PR 28 review: the provider refused the request itself with a 4xx
    other than 404 (ModelNotFoundError) and 429 (RateLimitedError): a bad
    request shape, a permission or a billing problem, a content refusal.
    Deterministic, so never retried (I4: "no automatic retry"), by the
    adapter or by the workflow retry policy, which treats it as terminal.

    Before this class the adapter re-raised the SDK's own ClientError,
    which is not an LLMError: no agent caught it, no trace row was written,
    and the task failed as an unknown error. The message carries the HTTP
    status and the provider's error kind only (for example "400
    INVALID_ARGUMENT"), never the provider's message, which can quote the
    request."""

    def __init__(self, status_code: int | None, kind: str) -> None:
        super().__init__(f"provider rejected the request: HTTP {status_code} {kind}")
        self.status_code = status_code
        self.kind = kind


class RateLimitedError(LLMError):
    """Provider returned 429. I4: 'rate limit: provider-specified delay' -
    carries retry_after_seconds when the provider supplied one, else the
    adapter's caller falls back to its own exponential backoff."""

    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class TransientProviderError(LLMError):
    """Network/5xx-class failure - I4: exponential backoff, max 3 attempts."""


class LLMTimeoutError(LLMError):
    """Phase 4: one HTTP request to the provider took longer than its
    request timeout, or one logical call (with the adapter's own retries and
    backoff) passed its overall deadline. Not retried by the adapter: the
    deadline already spans its retries."""


class MalformedOutputError(LLMError):
    """The model's response didn't validate against the requested schema.
    I4: 'one repair attempt, then fail task' - the caller is expected to
    retry at most once with the validation error appended to the prompt,
    not loop indefinitely."""


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    """What every LLMClient call returns - the parsed/validated payload plus
    everything E1 requires an agent to record (model, tokens, latency, cost).
    Callers persist this into agent_traces; the client itself never writes to
    the database (A4.1: agents call domain services, not the reverse, and the
    LLM client is lower-level than an agent)."""

    parsed: T
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    # The whole call's wall time, retries and backoff included (PR 28).
    latency_ms: int
    estimated_cost_usd: float | None
    # Provider requests made, beside latency_ms; see CallMeasurement.
    attempts: int | None
    # PR 29: thinking tokens, apart from output_tokens; see CallMeasurement.
    thinking_tokens: int | None = None


class TraceMeasurements(TypedDict):
    """The agent_traces columns one call's outcome fills, named as
    AgentTraceRepository.create's parameters so a call site can pass
    **trace_measurements(outcome)."""

    provider: str | None
    latency_ms: int | None
    attempts: int | None
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost_usd: float | None
    thinking_tokens: int | None


def trace_measurements(outcome: LLMResult[BaseModel] | LLMError | None) -> TraceMeasurements:
    """The trace fields for a completed call (its LLMResult) or a failed one
    (its LLMError's measurement). Every agent's _write_trace passes these, so
    the rule lives here once rather than in ten modules; a guard test
    (test_agent_traces_record_measurements.py) holds every call site to it.
    An error with no measurement, or no outcome at all, gives None for each:
    unknown, never a guessed 0."""
    source: LLMResult[BaseModel] | CallMeasurement | None
    if isinstance(outcome, LLMError):
        source = outcome.measurement
    else:
        source = outcome
    if source is None:
        return TraceMeasurements(
            provider=None,
            latency_ms=None,
            attempts=None,
            input_tokens=None,
            output_tokens=None,
            estimated_cost_usd=None,
            thinking_tokens=None,
        )
    return TraceMeasurements(
        provider=source.provider,
        latency_ms=source.latency_ms,
        attempts=source.attempts,
        input_tokens=source.input_tokens,
        output_tokens=source.output_tokens,
        estimated_cost_usd=source.estimated_cost_usd,
        thinking_tokens=source.thinking_tokens,
    )


def elapsed_ms(start: float) -> int:
    """Milliseconds since `start`, a time.monotonic() reading."""
    return int((time.monotonic() - start) * 1000)


class LLMClient(Protocol):
    """Provider-neutral per A2's locked choice. Every concrete adapter
    (GeminiClient, FakeLLMClient, and any later Anthropic/OpenAI adapter)
    implements this same one method - agents/requirements/extract.py and
    verify.py depend on this Protocol, never on a specific adapter class, so
    swapping providers later is a new adapter file with zero agent changes.
    """

    async def complete_structured[T: BaseModel](
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
    ) -> LLMResult[T]: ...


async def call_with_repair[T: BaseModel](
    client: LLMClient,
    *,
    agent_name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_schema: type[T],
) -> LLMResult[T]:
    """I4: when structured output is malformed, try one repair, then fail the task.
    Shared by every agent (extract.py, verify.py, and whatever B6+ adds)
    rather than each reimplementing the same one-retry-with-the-error-
    appended shape. A second MalformedOutputError propagates - this
    function does not loop, matching I4's "then fail task," not "then retry
    forever."

    PR 28: a repaired call is still one logical call with one trace row, so
    its measurement covers both requests. The malformed answer was billed
    like any other, so its tokens and cost are added in, not dropped:
    attempts, tokens and cost are the two calls' sums, and latency_ms is the
    wall time across both. A request whose measurement is not known makes
    each sum unknown (None), never a guess.
    """
    start = time.monotonic()
    try:
        return await client.complete_structured(
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
    except MalformedOutputError as exc:
        first = exc.measurement
        repaired_prompt = (
            f"{user_prompt}\n\n"
            f"Your previous response did not match the required schema: {exc}\n"
            "Respond again, strictly matching the schema this time."
        )
    try:
        result = await client.complete_structured(
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=repaired_prompt,
            response_schema=response_schema,
        )
    except LLMError as exc:
        exc.measurement = _both_requests(first, exc.measurement, elapsed_ms(start))
        raise
    second = CallMeasurement(
        provider=result.provider,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        thinking_tokens=result.thinking_tokens,
    )
    # Never None here (`second` is measured); `or second` only narrows the type.
    both = _both_requests(first, second, elapsed_ms(start)) or second
    return replace(
        result,
        latency_ms=both.latency_ms,
        attempts=both.attempts,
        input_tokens=both.input_tokens,
        output_tokens=both.output_tokens,
        estimated_cost_usd=both.estimated_cost_usd,
        thinking_tokens=both.thinking_tokens,
    )


def _sum_known[N: (int, float)](a: N | None, b: N | None) -> N | None:
    return None if a is None or b is None else a + b


def _both_requests(
    first: CallMeasurement | None, second: CallMeasurement | None, latency_ms: int
) -> CallMeasurement | None:
    """One measurement for a repaired call: see call_with_repair. With one
    request unmeasured, only the provider and the wall time are known."""
    known = second or first
    if known is None:
        return None
    if first is None or second is None:
        return CallMeasurement(provider=known.provider, latency_ms=latency_ms, attempts=None)
    return CallMeasurement(
        provider=second.provider,
        latency_ms=latency_ms,
        attempts=_sum_known(first.attempts, second.attempts),
        input_tokens=_sum_known(first.input_tokens, second.input_tokens),
        output_tokens=_sum_known(first.output_tokens, second.output_tokens),
        estimated_cost_usd=_sum_known(first.estimated_cost_usd, second.estimated_cost_usd),
        thinking_tokens=_sum_known(first.thinking_tokens, second.thinking_tokens),
    )
