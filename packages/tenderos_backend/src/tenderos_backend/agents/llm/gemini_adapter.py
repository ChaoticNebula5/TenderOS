import asyncio
import hashlib
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis

from tenderos_backend.agents.llm.client import (
    LLMResult,
    MalformedOutputError,
    ModelNotFoundError,
    RateLimitedError,
    TransientProviderError,
)

# Part P: verify this SDK surface (google-genai's Client/errors/types shape,
# and whether .aio.models.generate_content is still the async entry point)
# against the version uv actually resolves before trusting real eval-gate
# numbers - written against the SDK shape known at build time, not confirmed
# against a live install.
_MAX_TRANSIENT_RETRIES = 3
_BASE_BACKOFF_SECONDS = 5.0


def _cache_key(model: str, system_prompt: str, user_prompt: str, schema_name: str) -> str:
    raw = f"gemini|{model}|{schema_name}|{system_prompt}|{user_prompt}"
    return f"llm_cache:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


class GeminiClient:
    """First concrete LLMClient adapter (this project's own choice - A2's
    literal locked-choice cell names Anthropic; flagged in the B5 plan and
    commit message). Owns everything provider-specific: the SDK call itself,
    error-shape translation into agents/llm/client.py's exception hierarchy,
    I4's transient-error backoff, and response caching - none of that leaks
    into agents/requirements/extract.py or verify.py, which only ever see the
    provider-neutral LLMClient Protocol.
    """

    def __init__(self, api_key: str, redis_client: Redis, cache_ttl_seconds: int) -> None:
        self._client = genai.Client(api_key=api_key)
        self._redis = redis_client
        self._cache_ttl_seconds = cache_ttl_seconds

    async def complete_structured[T: BaseModel](
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
    ) -> LLMResult[T]:
        cache_key = _cache_key(model, system_prompt, user_prompt, response_schema.__name__)
        cached = await self._redis.get(cache_key)
        if cached is not None:
            return LLMResult(
                parsed=response_schema.model_validate_json(cached),
                provider="gemini",
                model=model,
                input_tokens=None,
                output_tokens=None,
                latency_ms=0,
                estimated_cost_usd=None,
            )

        start = time.monotonic()
        response = await self._call_with_retry(model, system_prompt, user_prompt, response_schema)
        latency_ms = int((time.monotonic() - start) * 1000)

        response_text = response.text
        if response_text is None:
            raise MalformedOutputError(f"{agent_name}: empty response body")

        try:
            parsed = response_schema.model_validate_json(response_text)
        except ValidationError as exc:
            raise MalformedOutputError(
                f"{agent_name}: response did not match {response_schema.__name__}: {exc}"
            ) from exc

        usage = response.usage_metadata
        await self._redis.set(cache_key, response_text, ex=self._cache_ttl_seconds)

        return LLMResult(
            parsed=parsed,
            provider="gemini",
            model=model,
            input_tokens=usage.prompt_token_count if usage else None,
            output_tokens=usage.candidates_token_count if usage else None,
            latency_ms=latency_ms,
            # No published per-token Gemini pricing is wired in yet - cost
            # tracking (I5's MAX_TENDER_COST_USD budget) is real B11 scope;
            # agent_traces.estimated_cost_usd stays NULL until that block
            # adds a provider price table rather than a guessed constant here.
            estimated_cost_usd=None,
        )

    async def _call_with_retry(
        self, model: str, system_prompt: str, user_prompt: str, response_schema: type[BaseModel]
    ) -> genai_types.GenerateContentResponse:
        attempt = 0
        while True:
            try:
                return await self._client.aio.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        response_mime_type="application/json",
                        response_schema=response_schema,
                        temperature=0,
                    ),
                )
            except genai_errors.ClientError as exc:
                status = getattr(exc, "code", None)
                if status == 404:
                    raise ModelNotFoundError(f"Model not found: {model}") from exc
                if status == 429:
                    retry_after = _retry_after_seconds(exc)
                    if attempt >= _MAX_TRANSIENT_RETRIES:
                        raise RateLimitedError(str(exc), retry_after) from exc
                    await asyncio.sleep(retry_after or _BASE_BACKOFF_SECONDS * (2**attempt))
                    attempt += 1
                    continue
                # Any other 4xx is a deterministic request problem (I4: "no
                # automatic retry") - a bad request shape, not something a
                # second attempt fixes.
                raise
            except genai_errors.ServerError as exc:
                if attempt >= _MAX_TRANSIENT_RETRIES:
                    raise TransientProviderError(str(exc)) from exc
                await asyncio.sleep(_BASE_BACKOFF_SECONDS * (2**attempt))
                attempt += 1


def _retry_after_seconds(exc: genai_errors.ClientError) -> float | None:
    # Provider-specified delay (I4) if the SDK/response surfaces one -
    # verify the actual attribute/header name against the installed SDK
    # (Part P); falls back to the adapter's own backoff if absent.
    return getattr(exc, "retry_after", None)
