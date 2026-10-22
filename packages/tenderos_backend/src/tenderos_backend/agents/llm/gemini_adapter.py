import asyncio
import hashlib
import time

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis

from tenderos_backend.agents.llm.client import (
    LLMResult,
    LLMTimeoutError,
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

# Phase 4 LLM timeouts, design numbers (not measured). Before these, a call
# had no bound at all: the client was built with no http_options, so a hung
# provider connection could hold a worker job (or, before the move to the
# worker, an HTTP request) indefinitely.
# - _HTTP_TIMEOUT_MS: each HTTP request to the provider. google-genai 2.17.0
#   takes it in milliseconds (types.HttpOptions.timeout) and, with no retry
#   options set, makes one attempt and re-raises httpx.TimeoutException.
# - _CALL_DEADLINE_SECONDS: one logical call, including this adapter's own
#   rate-limit and 5xx retries and their 5, 10 and 20 s backoffs.
_HTTP_TIMEOUT_MS = 60_000
_CALL_DEADLINE_SECONDS = 180.0

# Post-B16: agent_traces.estimated_cost_usd was NULL on all 2,455 rows ever
# written, not because the SDK doesn't return token counts (it does -
# response.usage_metadata below, already captured into input_tokens/
# output_tokens) but because no price table existed to turn a token count
# into a dollar figure. This is that table. A HARDCODED APPROXIMATION, NOT
# A BILLING INTEGRATION: real (USD per 1M tokens, text/image/video input,
# not audio - every call this project makes is text) as published at
# https://ai.google.dev/gemini-api/docs/pricing, checked directly rather
# than guessed, but Google can and does change these without this file
# knowing - treat estimated_cost_usd as a rough order-of-magnitude signal
# for relative cost comparison across runs, never as a real invoice figure.
# Keyed by the exact model id string the API call actually used, not by
# this project's role-based EXTRACTOR_MODEL/VERIFIER_MODEL/etc. env var
# names - a role can point at any model string, and pricing is a property
# of the model, not the role. A model id not in this table (e.g. the
# "changeme" placeholder REASONING_MODEL/WRITER_MODEL/CRITIC_MODEL still
# default to, which errors out as ModelNotFoundError before ever reaching
# here anyway) leaves estimated_cost_usd None, honestly, rather than
# guessing another model's price for it.
_PRICE_PER_MILLION_TOKENS_USD: dict[str, tuple[float, float]] = {
    # (input_rate, output_rate), both USD per 1,000,000 tokens.
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}


def _estimate_cost_usd(
    model: str, input_tokens: int | None, output_tokens: int | None
) -> float | None:
    if input_tokens is None or output_tokens is None:
        return None
    rates = _PRICE_PER_MILLION_TOKENS_USD.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate


def _cache_key(model: str, system_prompt: str, user_prompt: str, schema_name: str) -> str:
    raw = f"gemini|{model}|{schema_name}|{system_prompt}|{user_prompt}"
    return f"llm_cache:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _redacted_validation_summary(exc: ValidationError) -> str:
    """B15: ValidationError's own __str__ embeds each error's input_value
    verbatim - the raw text that failed schema validation, which is
    source-derived (a document chunk, an evidence excerpt) rather than
    ever fabricated, and has no reason to leave this adapter. Built from
    exc.errors() instead of str(exc) - field location, error type, and
    message are enough for a human operator or call_with_repair's own
    retry prompt (client.py, which interpolates MalformedOutputError's
    message directly into a follow-up LLM call) to act on; the actual
    failing value is not carried forward into either.
    """
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']} [{err['type']}]"
        for err in exc.errors(include_url=False)
    )


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
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
        )
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
        try:
            response = await asyncio.wait_for(
                self._call_with_retry(model, system_prompt, user_prompt, response_schema),
                timeout=_CALL_DEADLINE_SECONDS,
            )
        except TimeoutError as exc:
            raise LLMTimeoutError(
                f"{agent_name}: no answer within the {_CALL_DEADLINE_SECONDS:.0f} s call deadline"
            ) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        response_text = response.text
        if response_text is None:
            raise MalformedOutputError(f"{agent_name}: empty response body")

        try:
            parsed = response_schema.model_validate_json(response_text)
        except ValidationError as exc:
            # `from None`, not `from exc` - chaining would still attach the
            # original ValidationError (and its own unredacted input_value)
            # to this exception's traceback, visible to anything that later
            # logs it via logger.exception(), even though this exception's
            # own message is now safe. See _redacted_validation_summary's
            # own docstring for what it strips and why.
            raise MalformedOutputError(
                f"{agent_name}: response did not match {response_schema.__name__}: "
                f"{_redacted_validation_summary(exc)}"
            ) from None

        usage = response.usage_metadata
        await self._redis.set(cache_key, response_text, ex=self._cache_ttl_seconds)

        input_tokens = usage.prompt_token_count if usage else None
        output_tokens = usage.candidates_token_count if usage else None

        return LLMResult(
            parsed=parsed,
            provider="gemini",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            # See _PRICE_PER_MILLION_TOKENS_USD's own module-level comment -
            # a hardcoded approximation from published pricing, not a real
            # billing integration. None (not 0.0) for a cache hit (no fresh
            # token counts above) or an unmapped model id - honestly absent,
            # not a false zero-cost claim.
            estimated_cost_usd=_estimate_cost_usd(model, input_tokens, output_tokens),
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
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"provider request timed out after {_HTTP_TIMEOUT_MS // 1000} s"
                ) from exc
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
