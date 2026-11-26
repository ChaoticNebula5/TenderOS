import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from pydantic import BaseModel, ValidationError
from redis.asyncio import Redis

from tenderos_backend.agents.llm.client import (
    CallMeasurement,
    DailyQuotaExhaustedError,
    LLMError,
    LLMResult,
    LLMTimeoutError,
    MalformedOutputError,
    ModelNotFoundError,
    ProviderRequestRejectedError,
    RateLimitedError,
    TransientProviderError,
    elapsed_ms,
)

# Part P: verify this SDK surface (google-genai's Client/errors/types shape,
# and whether .aio.models.generate_content is still the async entry point)
# against the version uv actually resolves before trusting real eval-gate
# numbers - written against the SDK shape known at build time, not confirmed
# against a live install.
_MAX_TRANSIENT_RETRIES = 3
_BASE_BACKOFF_SECONDS = 5.0

logger = logging.getLogger(__name__)

# Phase 4 LLM timeouts, design numbers (not measured). Before these, a call
# had no bound at all: the client was built with no http_options, so a hung
# provider connection could hold a worker job (or, before the move to the
# worker, an HTTP request) indefinitely.
# - _HTTP_TIMEOUT_MS: each HTTP request to the provider. google-genai 2.31.0
#   (re-read in its types.py and _api_client.py for PR 34) takes it in
#   milliseconds (types.HttpOptions.timeout) and, with no retry options set,
#   makes one attempt and re-raises httpx.TimeoutException.
# - _CALL_DEADLINE_SECONDS: one logical call, including this adapter's own
#   rate-limit and 5xx retries and their 5, 10 and 20 s backoffs.
_HTTP_TIMEOUT_MS = 60_000
_CALL_DEADLINE_SECONDS = 180.0

# PR 34: a 429's own wait is taken only when it ends this long before the
# call deadline, so a rate-limit wait never turns into an LLMTimeoutError;
# a longer one is raised as RateLimitedError with the provider's wait, for
# the workflow retry policy to honour.
_DEADLINE_MARGIN_SECONDS = 5.0

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
# "changeme" placeholder REASONING_MODEL still defaults to, which errors
# out as ModelNotFoundError before ever reaching here anyway) leaves
# estimated_cost_usd None, honestly, rather than guessing another model's
# price for it, and factory.log_llm_provider warns at startup.
#
# PR 34: every row re-read on 2026-11-25 at
# https://aistudio.google.com/docs/pricing (paid standard tier, text input).
# gemini-3.5-flash and gemini-3.7-flash were removed: neither is on the page,
# so their prices could not be checked. What the table does not model:
# - gemini-3.8-flash doubles from 2027-01-01 ($1.50 in, $7.50 out);
# - gemini-2.5-pro and gemini-3.1-pro-preview charge more above 200k prompt
#   tokens ($2.50/$15.00 and $4.00/$18.00); no call here is near that size;
# - gemini-2.5-pro is deprecated and routed to gemini-3.8-flash from
#   2026-12-01, and gemini-3.1-pro-preview is not on the free tier.
_PRICE_PER_MILLION_TOKENS_USD: dict[str, tuple[float, float]] = {
    # (input_rate, output_rate), both USD per 1,000,000 tokens.
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
}


def has_price(model: str) -> bool:
    """Whether a call to `model` can record a cost (PR 29: the startup
    warning in factory.log_llm_provider names a configured model that
    cannot)."""
    return model in _PRICE_PER_MILLION_TOKENS_USD


def _estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    thinking_tokens: int | None = None,
) -> float | None:
    """PR 29: thinking tokens are billed at the output rate. Google's
    pricing page (https://ai.google.dev/gemini-api/docs/pricing, which now
    redirects to https://aistudio.google.com/docs/pricing; read 2026-11-21)
    labels each model's output row "Output price (including thinking
    tokens)". So the answer's tokens and the thinking tokens are priced
    together at the output rate, while agent_traces keeps them in two
    columns (output_tokens, thinking_tokens). No thinking count (None) is
    priced as none."""
    if input_tokens is None or output_tokens is None:
        return None
    rates = _PRICE_PER_MILLION_TOKENS_USD.get(model)
    if rates is None:
        return None
    input_rate, output_rate = rates
    billed_output = output_tokens + (thinking_tokens or 0)
    return (input_tokens / 1_000_000) * input_rate + (billed_output / 1_000_000) * output_rate


def _generation_config(
    system_prompt: str, response_schema: type[BaseModel]
) -> genai_types.GenerateContentConfig:
    """The one place a request's config is built, so the cache key below
    hashes exactly what is sent (PR 35)."""
    return genai_types.GenerateContentConfig(
        system_instruction=system_prompt,
        response_mime_type="application/json",
        response_schema=response_schema,
        temperature=0,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _schema_hash(response_schema: type[BaseModel]) -> str:
    """PR 35: the schema's JSON schema, serialised with sorted keys, so two
    equal versions hash the same in any process and a changed field does not.
    The class name alone let a schema changed under the same name hit an
    answer made for the old one."""
    canonical = _canonical_json(response_schema.model_json_schema())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _generation_settings(config: genai_types.GenerateContentConfig) -> str:
    """PR 35: every generation setting the request carries (temperature, the
    response type, and any output limit, thinking or safety setting added
    later), with the prompt and the schema left to their own parts of the
    key. Read off the config itself, so a new setting needs no change here."""
    settings_sent = config.model_dump(
        mode="json", exclude_none=True, exclude={"system_instruction", "response_schema"}
    )
    return _canonical_json(settings_sent)


# PR 35: the key format. Bumped only when the key's layout changes; what a
# request is (model, schema, settings, prompts) is in the hash.
_CACHE_KEY_VERSION = "v2"


def _cache_key(
    organization_id: uuid.UUID | None,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_schema: type[BaseModel],
    config: genai_types.GenerateContentConfig,
) -> str:
    """PR 35: one organization's answers never serve another's call. The
    organization id is in the clear, outside the hash, so one organization's
    entries can be found by prefix (to delete them, item 119). A client with
    no organization (only scripts that build GeminiClient themselves; every
    app call goes through the factory, which requires one) has a namespace
    of its own. The hashed fields are a JSON array, so no prompt text can
    run into the next field the way a "|"-joined string could."""
    scope = f"org:{organization_id}" if organization_id is not None else "none"
    raw = _canonical_json(
        [
            "gemini",
            model,
            _schema_hash(response_schema),
            _generation_settings(config),
            system_prompt,
            user_prompt,
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"llm_cache:{_CACHE_KEY_VERSION}:{scope}:{digest}"


@dataclass
class _Requests:
    """PR 28: the provider requests one call has made so far. A mutable
    counter passed into _call_with_retry, not that coroutine's return value:
    when the call deadline cancels the retry loop, nothing is returned, and
    the count must still reach the LLMTimeoutError's measurement.

    PR 34: also the requests answered 429 and the time spent waiting on
    them, for the same reason."""

    made: int = 0
    rate_limited: int = 0
    rate_limit_wait_ms: int = 0


# PR 34: what a 429 says about itself. google-genai keeps the response body
# on the error (APIError.details); Google's 429 body is a google.rpc.Status
# whose details list may hold a RetryInfo (retryDelay, a Duration such as
# "37s" or "1.5s") and a QuotaFailure (violations, each with a quotaId such
# as GenerateRequestsPerMinutePerProjectPerModel-FreeTier). Every value read
# from it must match its pattern, so nothing else from the body is kept.
_RETRY_INFO = "google.rpc.RetryInfo"
_QUOTA_FAILURE = "google.rpc.QuotaFailure"
_DETAIL_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_.]{0,127}")
_DURATION = re.compile(r"([0-9]{1,6}(?:\.[0-9]{1,9})?)s")
_QUOTA_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


@dataclass(frozen=True)
class RateLimitDetails:
    """What one 429's body says, as far as it can be read.

    - detail_types: the short type name of every entry in the body's details
      list ("unrecognised" for one that is not a dict with a plain @type);
    - retry_after_seconds: RetryInfo's retryDelay, None without a readable one;
    - quota_ids: every readable QuotaFailure violation's quotaId;
    - quota: "day" when any quota id is a per-day one, else "minute" when any
      is a per-minute one, else "unknown"."""

    detail_types: tuple[str, ...] = ()
    retry_after_seconds: float | None = None
    quota_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def quota(self) -> str:
        if any("PerDay" in quota_id for quota_id in self.quota_ids):
            return "day"
        if any("PerMinute" in quota_id for quota_id in self.quota_ids):
            return "minute"
        return "unknown"


def parse_rate_limit(exc: genai_errors.ClientError) -> RateLimitDetails:
    """The RetryInfo and QuotaFailure of a 429's body (see RateLimitDetails).
    Never raises: a body of another shape reads as nothing found."""
    body = getattr(exc, "details", None)
    error = body.get("error") if isinstance(body, dict) else None
    entries = error.get("details") if isinstance(error, dict) else None
    if not isinstance(entries, list):
        return RateLimitDetails()
    types: list[str] = []
    retry_after: float | None = None
    quota_ids: list[str] = []
    for entry in entries:
        raw = entry.get("@type") if isinstance(entry, dict) else None
        name = raw.rsplit("/", 1)[-1] if isinstance(raw, str) else ""
        if not _DETAIL_TYPE.fullmatch(name):
            types.append("unrecognised")
            continue
        types.append(name)
        if name == _RETRY_INFO:
            delay = entry.get("retryDelay")
            matched = _DURATION.fullmatch(delay) if isinstance(delay, str) else None
            if matched:
                retry_after = float(matched.group(1))
        elif name == _QUOTA_FAILURE:
            violations = entry.get("violations")
            for violation in violations if isinstance(violations, list) else []:
                quota_id = violation.get("quotaId") if isinstance(violation, dict) else None
                if isinstance(quota_id, str) and _QUOTA_ID.fullmatch(quota_id):
                    quota_ids.append(quota_id)
    return RateLimitDetails(tuple(types), retry_after, tuple(quota_ids))


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

    def __init__(
        self,
        api_key: str,
        redis_client: Redis,
        cache_ttl_seconds: int,
        organization_id: uuid.UUID | None = None,
    ) -> None:
        self._client = genai.Client(
            api_key=api_key,
            http_options=genai_types.HttpOptions(timeout=_HTTP_TIMEOUT_MS),
        )
        self._redis = redis_client
        self._cache_ttl_seconds = cache_ttl_seconds
        # PR 35: the organization every call of this client is made for; its
        # cache entries are that organization's alone (see _cache_key).
        self._organization_id = organization_id

    async def complete_structured[T: BaseModel](
        self,
        *,
        agent_name: str,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: type[T],
    ) -> LLMResult[T]:
        # PR 28: latency_ms is the whole call's wall time from here, the
        # cache lookup, every retry and every backoff included; `attempts`
        # beside it says how many provider requests that time covered.
        start = time.monotonic()
        config = _generation_config(system_prompt, response_schema)
        cache_key = _cache_key(
            self._organization_id, model, system_prompt, user_prompt, response_schema, config
        )
        cached = await self._redis.get(cache_key)
        cached_parsed: T | None = None
        if cached is not None:
            try:
                cached_parsed = response_schema.model_validate_json(cached)
            except ValidationError:
                # PR 35: an entry that no longer validates is a miss, not a
                # failed call (a ValidationError is no LLMError, so it would
                # leave unmeasured, with no trace row). Deleted first, so a
                # provider failure below does not leave it to fail the next
                # call too. The error itself is not logged: it quotes the
                # cached answer.
                logger.warning(
                    "llm cache: agent=%s model=%s a cached answer did not match %s; "
                    "deleted, the provider is called",
                    agent_name,
                    model,
                    response_schema.__name__,
                )
                await self._redis.delete(cache_key)
        if cached_parsed is not None:
            # A cache hit makes no provider request (attempts 0), so it is
            # known to cost nothing: 0.0, not None (PR 28 review). Tokens
            # stay None: none were counted for this call. Rows from before
            # migration 036 wrote None here and have no attempts, so the
            # report still shows them as unknown.
            return LLMResult(
                parsed=cached_parsed,
                provider="gemini",
                model=model,
                input_tokens=None,
                output_tokens=None,
                latency_ms=elapsed_ms(start),
                estimated_cost_usd=0.0,
                attempts=0,
                rate_limited_requests=0,
                rate_limit_wait_ms=0,
            )

        requests = _Requests()
        deadline = start + _CALL_DEADLINE_SECONDS
        input_tokens: int | None = None
        output_tokens: int | None = None
        thinking_tokens: int | None = None
        try:
            try:
                response = await asyncio.wait_for(
                    self._call_with_retry(
                        agent_name,
                        model,
                        user_prompt,
                        config,
                        requests,
                        deadline,
                    ),
                    timeout=_CALL_DEADLINE_SECONDS,
                )
            except TimeoutError as exc:
                raise LLMTimeoutError(
                    f"{agent_name}: no answer within the "
                    f"{_CALL_DEADLINE_SECONDS:.0f} s call deadline"
                ) from exc

            # Read before the answer is checked: a malformed answer was
            # billed too, and its measurement keeps these tokens.
            usage = response.usage_metadata
            input_tokens = usage.prompt_token_count if usage else None
            output_tokens = usage.candidates_token_count if usage else None
            # PR 29: the SDK leaves thoughts_token_count unset when the model
            # did not think, so a response with usage and no count thought 0.
            thinking_tokens = (usage.thoughts_token_count or 0) if usage else None

            response_text = response.text
            if response_text is None:
                raise MalformedOutputError(f"{agent_name}: empty response body")

            try:
                parsed = response_schema.model_validate_json(response_text)
            except ValidationError as exc:
                # `from None`, not `from exc` - chaining would still attach
                # the original ValidationError (and its own unredacted
                # input_value) to this exception's traceback, visible to
                # anything that later logs it via logger.exception(), even
                # though this exception's own message is now safe. See
                # _redacted_validation_summary's own docstring for what it
                # strips and why.
                raise MalformedOutputError(
                    f"{agent_name}: response did not match {response_schema.__name__}: "
                    f"{_redacted_validation_summary(exc)}"
                ) from None
        except LLMError as exc:
            # PR 28: every failure this adapter raises leaves with what the
            # call measured, so the agent's trace row keeps the provider,
            # latency and attempts. Every provider failure is an LLMError
            # (a 4xx other than 404 and 429 is ProviderRequestRejectedError).
            # An exception that is not one passes through unmeasured, and
            # no agent writes a row for it.
            exc.measurement = CallMeasurement(
                provider="gemini",
                latency_ms=elapsed_ms(start),
                attempts=requests.made,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=_estimate_cost_usd(
                    model, input_tokens, output_tokens, thinking_tokens
                ),
                thinking_tokens=thinking_tokens,
                rate_limited_requests=requests.rate_limited,
                rate_limit_wait_ms=requests.rate_limit_wait_ms,
            )
            raise

        await self._redis.set(cache_key, response_text, ex=self._cache_ttl_seconds)

        return LLMResult(
            parsed=parsed,
            provider="gemini",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms(start),
            # See _PRICE_PER_MILLION_TOKENS_USD's own module-level comment -
            # a hardcoded approximation from published pricing, not a real
            # billing integration. None (not 0.0) for an unmapped model id or
            # a response with no usage - honestly absent, not a false
            # zero-cost claim. (A cache hit returns 0.0 above: it made no
            # request, so its cost is known. Before PR 28's review it was
            # None too.) Thinking tokens are priced as output (PR 29; see
            # _estimate_cost_usd).
            estimated_cost_usd=_estimate_cost_usd(
                model, input_tokens, output_tokens, thinking_tokens
            ),
            attempts=requests.made,
            thinking_tokens=thinking_tokens,
            rate_limited_requests=requests.rate_limited,
            rate_limit_wait_ms=requests.rate_limit_wait_ms,
        )

    async def _call_with_retry(
        self,
        agent_name: str,
        model: str,
        user_prompt: str,
        config: genai_types.GenerateContentConfig,
        requests: _Requests,
        deadline: float,
    ) -> genai_types.GenerateContentResponse:
        """I4's retries: a 429 and a 5xx are retried up to
        _MAX_TRANSIENT_RETRIES times, anything else is raised at once.

        PR 34, a 429: the wait is the provider's own RetryInfo delay when the
        body has one (until PR 34 it was never read: google-genai's errors have
        no retry_after attribute), else the 5, 10 and 20 s backoff. A per-day
        quota is raised at once as DailyQuotaExhaustedError, since no wait
        inside one call can clear it. A wait that would end within
        _DEADLINE_MARGIN_SECONDS of `deadline` is not taken: the
        RateLimitedError carries it to the workflow retry policy instead.
        Each 429 is counted and each wait timed (_Requests), and every retry
        logs one WARNING line (status, quota, wait, agent, model; never the
        prompt or the provider's message)."""
        attempt = 0
        while True:
            requests.made += 1
            try:
                return await self._client.aio.models.generate_content(
                    model=model,
                    contents=user_prompt,
                    # PR 35: built once per call (_generation_config), the
                    # same object the cache key hashed.
                    config=config,
                )
            except genai_errors.ClientError as exc:
                status = getattr(exc, "code", None)
                if status == 404:
                    raise ModelNotFoundError(f"Model not found: {model}") from exc
                if status == 429:
                    requests.rate_limited += 1
                    limit = parse_rate_limit(exc)
                    _warn_if_not_understood(agent_name, model, limit)
                    if limit.quota == "day":
                        logger.warning(
                            "llm rate limit: agent=%s model=%s status=429 quota=day; "
                            "not retried, the quota resets with the provider's day",
                            agent_name,
                            model,
                        )
                        raise DailyQuotaExhaustedError(str(exc), limit.retry_after_seconds) from exc
                    wait = (
                        limit.retry_after_seconds
                        if limit.retry_after_seconds is not None
                        else _BASE_BACKOFF_SECONDS * (2**attempt)
                    )
                    left = deadline - time.monotonic() - _DEADLINE_MARGIN_SECONDS
                    if attempt >= _MAX_TRANSIENT_RETRIES or wait > left:
                        raise RateLimitedError(str(exc), limit.retry_after_seconds) from exc
                    _log_retry(agent_name, model, 429, limit.quota, wait, attempt + 1)
                    await _timed_wait(wait, requests)
                    attempt += 1
                    continue
                # Any other 4xx is a deterministic request problem (I4: "no
                # automatic retry") - a bad request shape, not something a
                # second attempt fixes. PR 28 review: raised as an LLMError,
                # so the agent records the call and the workflow retry
                # policy treats it as terminal, where the SDK's own
                # ClientError wrote no trace row and failed as unknown.
                # `from None`: the ClientError's text holds the provider's
                # message, which can quote the request (see
                # ProviderRequestRejectedError).
                raise ProviderRequestRejectedError(status, _error_kind(exc)) from None
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(
                    f"provider request timed out after {_HTTP_TIMEOUT_MS // 1000} s"
                ) from exc
            except genai_errors.ServerError as exc:
                if attempt >= _MAX_TRANSIENT_RETRIES:
                    raise TransientProviderError(str(exc)) from exc
                wait = _BASE_BACKOFF_SECONDS * (2**attempt)
                _log_retry(agent_name, model, getattr(exc, "code", None), "-", wait, attempt + 1)
                await asyncio.sleep(wait)
                attempt += 1


def _log_retry(
    agent_name: str, model: str, status: int | None, quota: str, wait: float, retry: int
) -> None:
    """PR 34: one WARNING per retried request, so the run's logs say which
    calls waited, on what, and for how long."""
    logger.warning(
        "llm retry: agent=%s model=%s status=%s quota=%s wait_s=%.1f retry=%d of %d",
        agent_name,
        model,
        status,
        quota,
        wait,
        retry,
        _MAX_TRANSIENT_RETRIES,
    )


def _warn_if_not_understood(agent_name: str, model: str, limit: RateLimitDetails) -> None:
    """PR 34 (user, Q4): a 429 with no readable RetryInfo or no readable
    QuotaFailure logs one WARNING naming the detail types it did find (type
    names only), so a wrong guess about the body's shape shows on the first
    real 429 instead of quietly falling back to the fixed waits."""
    missing = [
        name
        for name, found in (
            ("RetryInfo", limit.retry_after_seconds is not None),
            ("QuotaFailure", bool(limit.quota_ids)),
        )
        if not found
    ]
    if missing:
        logger.warning(
            "llm 429 not understood: agent=%s model=%s no readable %s; detail types found: %s",
            agent_name,
            model,
            " or ".join(missing),
            ", ".join(limit.detail_types) or "none",
        )


async def _timed_wait(seconds: float, requests: _Requests) -> None:
    """Sleep on a 429 and add the time slept to the call's rate-limit wait,
    also when the call deadline cancels the sleep part way."""
    started = time.monotonic()
    try:
        await asyncio.sleep(seconds)
    finally:
        requests.rate_limit_wait_ms += elapsed_ms(started)


_ERROR_KIND = re.compile(r"[A-Z][A-Z_]{0,63}")


def _error_kind(exc: genai_errors.ClientError) -> str:
    """The provider's error kind (google-genai's APIError.status, the
    google.rpc code name such as INVALID_ARGUMENT or PERMISSION_DENIED),
    kept only when it has that shape, so nothing else from the response
    can reach the error's message; "unknown" otherwise."""
    kind = getattr(exc, "status", None)
    return kind if isinstance(kind, str) and _ERROR_KIND.fullmatch(kind) else "unknown"
