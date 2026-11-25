"""PR 34: GeminiClient's 429 handling (agents/llm/gemini_adapter.py).

- parse_rate_limit reads a 429 body's RetryInfo delay and QuotaFailure quota
  ids, keeping only values of the expected shape;
- a per-minute 429 waits the provider's own delay, and the call records the
  429s and the time waited (rate_limited_requests, rate_limit_wait_ms);
- a per-day 429 is not retried: DailyQuotaExhaustedError at once;
- a wait that would run into the call deadline is not taken;
- a 429 with no readable RetryInfo or QuotaFailure falls back to the fixed
  backoff and logs one WARNING naming the detail types found, nothing else;
- every retry logs one WARNING line; a 5xx backoff is not a rate-limit wait.

No network and no real LLM cost: the SDK's generate_content is replaced by
a stand-in, and Redis by a cache that is always empty. The api key is fake.
"""

import logging
import time
from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel
from tenderos_backend.agents.llm import gemini_adapter
from tenderos_backend.agents.llm.client import DailyQuotaExhaustedError, RateLimitedError
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient, parse_rate_limit

# Windows' monotonic clock ticks about every 16 ms.
_TICK_SLACK_MS = 20

_LOGGER = "tenderos_backend.agents.llm.gemini_adapter"
_PER_MINUTE = "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
_PER_DAY = "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
# Text that must never reach a log line: the provider's own message.
_PROVIDER_MESSAGE = "quota text that quotes the request"


class _Answer(BaseModel):
    text: str


class _EmptyCache:
    async def get(self, key: str) -> None:
        return None

    async def set(self, *args, **kwargs) -> None:
        return None


def _client(generate_content) -> GeminiClient:
    client = GeminiClient(api_key="fake-key", redis_client=_EmptyCache(), cache_ttl_seconds=60)
    client._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    return client


async def _ask(client: GeminiClient):
    return await client.complete_structured(
        agent_name="fake_agent",
        model="fake-model",
        system_prompt="fake system prompt",
        user_prompt="fake user prompt",
        response_schema=_Answer,
    )


def _response():
    return SimpleNamespace(
        text='{"text": "ok"}',
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=2, thoughts_token_count=None
        ),
    )


def _retry_info(delay: str) -> dict:
    return {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}


def _quota_failure(*quota_ids: str) -> dict:
    return {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": q, "quotaValue": "15"} for q in quota_ids],
    }


def _limited(*details: dict) -> genai_errors.ClientError:
    return genai_errors.ClientError(
        429,
        {
            "error": {
                "code": 429,
                "message": _PROVIDER_MESSAGE,
                "status": "RESOURCE_EXHAUSTED",
                "details": list(details),
            }
        },
    )


def _limited_then_answers(error: genai_errors.ClientError, times: int = 1):
    requests = 0

    async def generate_content(**kwargs):
        nonlocal requests
        requests += 1
        if requests <= times:
            raise error
        return _response()

    return generate_content


# ---------------------------------------------------------------- the parser


def test_the_parser_reads_the_retry_delay_and_a_per_minute_quota():
    limit = parse_rate_limit(_limited(_quota_failure(_PER_MINUTE), _retry_info("37s")))

    assert limit.retry_after_seconds == 37.0
    assert limit.quota_ids == (_PER_MINUTE,)
    assert limit.quota == "minute"
    assert limit.detail_types == ("google.rpc.QuotaFailure", "google.rpc.RetryInfo")


def test_the_parser_reads_a_fractional_delay_and_a_per_day_quota():
    limit = parse_rate_limit(_limited(_retry_info("1.5s"), _quota_failure(_PER_MINUTE, _PER_DAY)))

    assert limit.retry_after_seconds == 1.5
    # Any per-day violation makes it a daily limit.
    assert limit.quota == "day"


def test_the_parser_keeps_nothing_of_an_unexpected_shape():
    odd = _limited(
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "soon"},
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": "has spaces; DROP"}, "not a dict"],
        },
        {"@type": "type.googleapis.com/<script>"},
        "not a dict",
    )

    limit = parse_rate_limit(odd)

    assert limit.retry_after_seconds is None
    assert limit.quota_ids == ()
    assert limit.quota == "unknown"
    assert limit.detail_types == (
        "google.rpc.RetryInfo",
        "google.rpc.QuotaFailure",
        "unrecognised",
        "unrecognised",
    )


@pytest.mark.parametrize(
    "body",
    [None, [], "text", {"error": "text"}, {"error": {"details": "text"}}, {"no_error": {}}],
)
def test_the_parser_reads_a_body_with_no_details_list_as_nothing_found(body):
    error = genai_errors.ClientError(429, {"error": {"message": "x"}})
    error.details = body

    limit = parse_rate_limit(error)

    assert limit == gemini_adapter.RateLimitDetails()


# ---------------------------------------------------------------- the adapter


@pytest.mark.asyncio
async def test_a_per_minute_429_waits_the_providers_delay_and_is_measured():
    error = _limited(_quota_failure(_PER_MINUTE), _retry_info("0.2s"))

    result = await _ask(_client(_limited_then_answers(error)))

    assert result.attempts == 2
    assert result.rate_limited_requests == 1
    # The provider's 0.2 s, not the fixed first backoff of 5 s.
    assert 200 - _TICK_SLACK_MS <= result.rate_limit_wait_ms < 2_000
    assert result.latency_ms >= result.rate_limit_wait_ms


@pytest.mark.asyncio
async def test_a_per_day_429_is_not_retried(caplog):
    requests = 0

    async def daily(**kwargs):
        nonlocal requests
        requests += 1
        raise _limited(_quota_failure(_PER_DAY), _retry_info("3600s"))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        with pytest.raises(DailyQuotaExhaustedError) as caught:
            await _ask(_client(daily))

    assert requests == 1
    assert isinstance(caught.value, RateLimitedError)
    assert caught.value.retry_after_seconds == 3600.0
    measurement = caught.value.measurement
    assert measurement is not None
    assert measurement.attempts == 1
    assert measurement.rate_limited_requests == 1
    assert measurement.rate_limit_wait_ms == 0
    assert any("quota=day" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_wait_past_the_call_deadline_is_left_to_the_task_retry(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_CALL_DEADLINE_SECONDS", 2.0)
    error = _limited(_quota_failure(_PER_MINUTE), _retry_info("30s"))

    started = time.monotonic()
    with pytest.raises(RateLimitedError) as caught:
        await _ask(_client(_limited_then_answers(error, times=5)))

    # Raised at once with the provider's wait, not slept into a timeout.
    assert time.monotonic() - started < 1.0
    assert not isinstance(caught.value, DailyQuotaExhaustedError)
    assert caught.value.retry_after_seconds == 30.0
    assert caught.value.measurement is not None
    assert caught.value.measurement.rate_limit_wait_ms == 0


@pytest.mark.asyncio
async def test_a_429_not_understood_falls_back_and_names_the_types_it_found(monkeypatch, caplog):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.05)
    error = _limited({"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "x"})

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        result = await _ask(_client(_limited_then_answers(error)))

    assert result.rate_limited_requests == 1
    assert result.rate_limit_wait_ms >= 50 - _TICK_SLACK_MS
    [line] = [r.getMessage() for r in caplog.records if "not understood" in r.getMessage()]
    assert "RetryInfo or QuotaFailure" in line
    assert "detail types found: google.rpc.ErrorInfo" in line
    assert _PROVIDER_MESSAGE not in caplog.text


@pytest.mark.asyncio
async def test_a_429_missing_only_its_retry_info_names_that_one(monkeypatch, caplog):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.01)
    error = _limited(_quota_failure(_PER_MINUTE))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _ask(_client(_limited_then_answers(error)))

    [line] = [r.getMessage() for r in caplog.records if "not understood" in r.getMessage()]
    assert "no readable RetryInfo;" in line
    assert "google.rpc.QuotaFailure" in line


@pytest.mark.asyncio
async def test_a_well_formed_429_logs_its_retry_and_nothing_not_understood(caplog):
    error = _limited(_quota_failure(_PER_MINUTE), _retry_info("0.01s"))

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        await _ask(_client(_limited_then_answers(error)))

    messages = [r.getMessage() for r in caplog.records]
    assert not any("not understood" in m for m in messages)
    [retry] = [m for m in messages if m.startswith("llm retry:")]
    assert "agent=fake_agent" in retry
    assert "model=fake-model" in retry
    assert "status=429" in retry
    assert "quota=minute" in retry
    assert "retry=1 of 3" in retry
    assert _PROVIDER_MESSAGE not in caplog.text


@pytest.mark.asyncio
async def test_a_5xx_backoff_is_logged_but_is_not_a_rate_limit_wait(monkeypatch, caplog):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.05)
    error = genai_errors.ServerError(503, {"error": {"message": "down", "status": "UNAVAILABLE"}})

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        result = await _ask(_client(_limited_then_answers(error)))

    assert result.attempts == 2
    assert result.rate_limited_requests == 0
    assert result.rate_limit_wait_ms == 0
    assert any("status=503" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_cache_hit_records_no_rate_limit():
    class _Hit(_EmptyCache):
        async def get(self, key: str) -> str:
            return '{"text": "cached"}'

    client = GeminiClient(api_key="fake-key", redis_client=_Hit(), cache_ttl_seconds=60)

    result = await _ask(client)

    assert (result.attempts, result.rate_limited_requests, result.rate_limit_wait_ms) == (0, 0, 0)
