from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class LLMError(Exception):
    """Base for every LLM-adapter failure - I4's retry policy branches on the
    concrete subclass, not on string-matching a provider's own exception."""


class ModelNotFoundError(LLMError):
    """The configured model ID doesn't exist for this provider/account. Never
    retried (I4 has no retry branch for a config error) and never mistaken
    for an extraction-quality problem - Part P: verify model IDs before the
    real eval run, this is what fires if that step gets skipped."""


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
    latency_ms: int
    estimated_cost_usd: float | None


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
    """I4: 'malformed structured output: one repair attempt, then fail task.'
    Shared by every agent (extract.py, verify.py, and whatever B6+ adds)
    rather than each reimplementing the same one-retry-with-the-error-
    appended shape. A second MalformedOutputError propagates - this
    function does not loop, matching I4's "then fail task," not "then retry
    forever."
    """
    try:
        return await client.complete_structured(
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
        )
    except MalformedOutputError as exc:
        repaired_prompt = (
            f"{user_prompt}\n\n"
            f"Your previous response did not match the required schema: {exc}\n"
            "Respond again, strictly matching the schema this time."
        )
        return await client.complete_structured(
            agent_name=agent_name,
            model=model,
            system_prompt=system_prompt,
            user_prompt=repaired_prompt,
            response_schema=response_schema,
        )
