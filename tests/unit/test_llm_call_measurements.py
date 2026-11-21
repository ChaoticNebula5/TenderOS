"""PR 28 (observability, LLM measurements): what one logical LLM call
measures, on success and on failure, for both adapters and for I4's repair.

- latency_ms is the whole call's wall time, retries and backoff included;
- attempts is the number of provider requests the call made (0 for a cache
  hit), beside it;
- a failed call carries the same measurement on its LLMError, so its
  agent_traces row keeps the provider, latency and attempts;
- the fake client measures its real latency and estimates tokens as
  characters / 4, rounded up, with cost 0.

No network and no real LLM cost: the Gemini SDK's generate_content is
replaced by a stand-in, Redis by an in-memory cache, and the api key is fake.
"""

import asyncio
import inspect
import time
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel
from tenderos_backend.agents.llm import gemini_adapter
from tenderos_backend.agents.llm.client import (
    CallMeasurement,
    LLMResult,
    LLMTimeoutError,
    MalformedOutputError,
    ModelNotFoundError,
    ProviderRequestRejectedError,
    RateLimitedError,
    TransientProviderError,
    call_with_repair,
    trace_measurements,
)
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.db.repositories.agent_traces import AgentTraceRepository

pytestmark = pytest.mark.asyncio

# Windows' monotonic clock ticks about every 16 ms (a 50 ms sleep measured
# 46 ms), so every lower bound on a measured latency leaves 20 ms of slack.
_TICK_SLACK_MS = 20

# A model id in gemini_adapter's price table: (1.50, 9.00) USD per 1M tokens.
_PRICED_MODEL = "gemini-3.5-flash"


class _Answer(BaseModel):
    text: str


class _Cache:
    def __init__(self, hit: str | None = None) -> None:
        self.hit = hit

    async def get(self, key: str) -> str | None:
        return self.hit

    async def set(self, *args, **kwargs) -> None:
        return None


def _gemini(generate_content, cache: _Cache | None = None) -> GeminiClient:
    client = GeminiClient(api_key="fake-key", redis_client=cache or _Cache(), cache_ttl_seconds=60)
    client._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    return client


def _response(text: str = '{"text": "ok"}', prompt_tokens: int = 1000, output_tokens: int = 200):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=output_tokens
        ),
    )


def _rate_limited() -> genai_errors.ClientError:
    return genai_errors.ClientError(429, {"error": {"message": "slow down", "status": "429"}})


def _server_error() -> genai_errors.ServerError:
    return genai_errors.ServerError(503, {"error": {"message": "unavailable", "status": "503"}})


async def _ask(client, model: str = _PRICED_MODEL, system: str = "system", user: str = "user"):
    return await client.complete_structured(
        agent_name="fake_agent",
        model=model,
        system_prompt=system,
        user_prompt=user,
        response_schema=_Answer,
    )


# ---------------------------------------------------------------- the fake


async def test_the_fake_estimates_tokens_as_characters_over_four_and_costs_nothing():
    fake = FakeLLMClient()
    fake.script("fake_agent", _Answer, [_Answer(text="ok")])

    result = await _ask(fake, model="any-model", system="s" * 10, user="u" * 31)

    # 41 prompt characters -> 11 tokens (rounded up); '{"text":"ok"}' is
    # 13 characters -> 4 tokens.
    assert result.input_tokens == 11
    assert result.output_tokens == 4
    assert result.estimated_cost_usd == 0.0
    assert result.provider == "fake"
    assert result.attempts == 1


async def test_the_fake_measures_its_own_latency():
    fake = FakeLLMClient()

    def slow_answer(user_prompt: str) -> _Answer:
        time.sleep(0.1)
        return _Answer(text="late")

    fake.set_fallback("fake_agent", _Answer, slow_answer)

    result = await _ask(fake, model="any-model")

    assert result.latency_ms >= 100 - _TICK_SLACK_MS


async def test_a_scripted_malformed_answer_carries_the_fakes_measurement():
    fake = FakeLLMClient()
    fake.script_malformed("fake_agent", _Answer)

    with pytest.raises(MalformedOutputError) as caught:
        await _ask(fake, model="any-model", system="s" * 4, user="u" * 4)

    measurement = caught.value.measurement
    assert measurement is not None
    assert measurement.provider == "fake"
    assert measurement.attempts == 1
    assert measurement.input_tokens == 2
    assert measurement.estimated_cost_usd == 0.0


# ---------------------------------------------------------------- Gemini


async def test_gemini_counts_every_request_and_times_the_backoff_into_latency(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.05)
    requests = 0

    async def limited_twice(**kwargs):
        nonlocal requests
        requests += 1
        if requests <= 2:
            raise _rate_limited()
        return _response()

    result = await _ask(_gemini(limited_twice))

    assert requests == 3
    assert result.attempts == 3
    # Backoffs of 0.05 s and 0.1 s sit inside the call's wall time.
    assert result.latency_ms >= 150 - _TICK_SLACK_MS
    assert result.input_tokens == 1000
    assert result.output_tokens == 200
    assert result.estimated_cost_usd == pytest.approx(0.0015 + 0.0018)


async def test_gemini_5xx_retries_exhausted_carry_provider_latency_and_attempts(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.02)

    async def always_down(**kwargs):
        raise _server_error()

    with pytest.raises(TransientProviderError) as caught:
        await _ask(_gemini(always_down))

    measurement = caught.value.measurement
    assert measurement is not None
    assert measurement.provider == "gemini"
    assert measurement.attempts == gemini_adapter._MAX_TRANSIENT_RETRIES + 1
    assert measurement.latency_ms >= 140 - _TICK_SLACK_MS  # 0.02 + 0.04 + 0.08 s of backoff
    assert measurement.input_tokens is None
    assert measurement.estimated_cost_usd is None


async def test_gemini_rate_limit_exhausted_carries_its_attempts(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_BASE_BACKOFF_SECONDS", 0.001)

    async def always_limited(**kwargs):
        raise _rate_limited()

    with pytest.raises(RateLimitedError) as caught:
        await _ask(_gemini(always_limited))

    assert caught.value.measurement is not None
    assert caught.value.measurement.attempts == gemini_adapter._MAX_TRANSIENT_RETRIES + 1


async def test_gemini_request_timeout_carries_one_attempt():
    async def hangs_up(**kwargs):
        raise httpx.ReadTimeout("fake read timeout")

    with pytest.raises(LLMTimeoutError) as caught:
        await _ask(_gemini(hangs_up))

    assert caught.value.measurement is not None
    assert caught.value.measurement.provider == "gemini"
    assert caught.value.measurement.attempts == 1


async def test_gemini_call_deadline_carries_the_attempts_started_and_the_wait(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_CALL_DEADLINE_SECONDS", 0.1)

    async def never_answers(**kwargs):
        await asyncio.sleep(10)

    with pytest.raises(LLMTimeoutError, match="call deadline") as caught:
        await asyncio.wait_for(_ask(_gemini(never_answers)), timeout=5)

    assert caught.value.measurement is not None
    assert caught.value.measurement.attempts == 1
    assert caught.value.measurement.latency_ms >= 100 - _TICK_SLACK_MS


async def test_gemini_unknown_model_carries_one_attempt():
    async def not_found(**kwargs):
        raise genai_errors.ClientError(404, {"error": {"message": "no model", "status": "404"}})

    with pytest.raises(ModelNotFoundError) as caught:
        await _ask(_gemini(not_found))

    assert caught.value.measurement is not None
    assert caught.value.measurement.attempts == 1


async def test_gemini_malformed_answer_keeps_the_tokens_it_was_billed_for():
    async def wrong_shape(**kwargs):
        return _response(text='{"unexpected": 1}', prompt_tokens=400, output_tokens=100)

    with pytest.raises(MalformedOutputError) as caught:
        await _ask(_gemini(wrong_shape))

    measurement = caught.value.measurement
    assert measurement is not None
    assert measurement.attempts == 1
    assert measurement.input_tokens == 400
    assert measurement.output_tokens == 100
    assert measurement.estimated_cost_usd == pytest.approx(0.0006 + 0.0009)


async def test_gemini_cache_hit_makes_no_request_and_is_known_to_cost_nothing():
    async def must_not_be_called(**kwargs):
        raise AssertionError("a cache hit must not reach the provider")

    result = await _ask(_gemini(must_not_be_called, cache=_Cache(hit='{"text": "cached"}')))

    assert result.parsed.text == "cached"
    assert result.attempts == 0
    # Known, not unknown: no request was made (PR 28 review). Tokens stay
    # None, since none were counted for this call.
    assert result.estimated_cost_usd == 0.0
    assert result.input_tokens is None
    assert result.output_tokens is None


# ---------------------------------------------------------------- a rejected request

# A provider message that quotes the request, as a 400 can.
_QUOTED_PROMPT = "Bidders must hold a valid tax clearance certificate"


@pytest.mark.parametrize(
    ("code", "kind"),
    [(400, "INVALID_ARGUMENT"), (403, "PERMISSION_DENIED"), (422, "FAILED_PRECONDITION")],
)
async def test_gemini_rejected_request_is_an_llm_error_not_retried_and_measured(code, kind):
    requests = 0

    async def rejects(**kwargs):
        nonlocal requests
        requests += 1
        raise genai_errors.ClientError(
            code, {"error": {"code": code, "message": f"bad: {_QUOTED_PROMPT}", "status": kind}}
        )

    with pytest.raises(ProviderRequestRejectedError) as caught:
        await _ask(_gemini(rejects), user=_QUOTED_PROMPT)

    error = caught.value
    assert requests == 1, "a rejected request is never retried by the adapter"
    assert error.status_code == code
    assert error.kind == kind
    assert str(error) == f"provider rejected the request: HTTP {code} {kind}"
    # Neither the prompt nor the provider's message: not in the text, and
    # not in a chained exception a log line could print.
    assert _QUOTED_PROMPT not in str(error)
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert error.measurement is not None
    assert error.measurement.provider == "gemini"
    assert error.measurement.attempts == 1
    assert error.measurement.estimated_cost_usd is None


async def test_gemini_rejected_request_keeps_only_an_error_kind_shaped_status():
    async def rejects_oddly(**kwargs):
        raise genai_errors.ClientError(
            400, {"error": {"message": "bad", "status": f"see: {_QUOTED_PROMPT}"}}
        )

    with pytest.raises(ProviderRequestRejectedError) as caught:
        await _ask(_gemini(rejects_oddly))

    assert caught.value.kind == "unknown"
    assert _QUOTED_PROMPT not in str(caught.value)


# ---------------------------------------------------------------- I4's repair


class _MeasuredStub:
    """A malformed first answer, then whatever `second` says: each with the
    measurement a real adapter would have attached."""

    def __init__(self, second, first_measured: bool = True) -> None:
        self.second = second
        self.first_measured = first_measured
        self.calls = 0

    async def complete_structured(
        self, *, agent_name, model, system_prompt, user_prompt, response_schema
    ):
        self.calls += 1
        if self.calls == 1:
            await asyncio.sleep(0.1)  # wall time the stub's own latency_ms leaves out
            exc = MalformedOutputError("schema mismatch")
            if self.first_measured:
                exc.measurement = CallMeasurement(
                    provider="gemini",
                    latency_ms=5,
                    attempts=2,
                    input_tokens=100,
                    output_tokens=20,
                    estimated_cost_usd=0.01,
                )
            raise exc
        if isinstance(self.second, Exception):
            raise self.second
        return self.second


def _second_result() -> LLMResult[_Answer]:
    return LLMResult(
        parsed=_Answer(text="repaired"),
        provider="gemini",
        model=_PRICED_MODEL,
        input_tokens=120,
        output_tokens=30,
        latency_ms=7,
        estimated_cost_usd=0.02,
        attempts=1,
    )


async def _repair(client):
    return await call_with_repair(
        client,
        agent_name="fake_agent",
        model=_PRICED_MODEL,
        system_prompt="system",
        user_prompt="user",
        response_schema=_Answer,
    )


async def test_a_repaired_call_adds_up_both_requests():
    result = await _repair(_MeasuredStub(_second_result()))

    assert result.parsed.text == "repaired"
    assert result.attempts == 3
    assert result.input_tokens == 220
    assert result.output_tokens == 50
    assert result.estimated_cost_usd == pytest.approx(0.03)
    # Wall time across both calls, not the sum of the two reported latencies.
    assert result.latency_ms >= 100 - _TICK_SLACK_MS


async def test_a_failed_repair_carries_both_requests_on_its_error():
    second_error = MalformedOutputError("still wrong")
    second_error.measurement = CallMeasurement(
        provider="gemini",
        latency_ms=6,
        attempts=1,
        input_tokens=130,
        output_tokens=25,
        estimated_cost_usd=0.015,
    )

    with pytest.raises(MalformedOutputError) as caught:
        await _repair(_MeasuredStub(second_error))

    measurement = caught.value.measurement
    assert caught.value is second_error
    assert measurement is not None
    assert measurement.attempts == 3
    assert measurement.input_tokens == 230
    assert measurement.estimated_cost_usd == pytest.approx(0.025)
    assert measurement.latency_ms >= 100 - _TICK_SLACK_MS


async def test_a_repair_after_an_unmeasured_answer_reports_unknown_not_a_guess():
    result = await _repair(_MeasuredStub(_second_result(), first_measured=False))

    assert result.parsed.text == "repaired"
    assert result.attempts is None
    assert result.input_tokens is None
    assert result.estimated_cost_usd is None


async def test_a_call_with_no_repair_keeps_the_adapters_own_measurement():
    fake = FakeLLMClient()
    fake.script("fake_agent", _Answer, [_Answer(text="ok")])

    result = await _repair(fake)

    assert result.attempts == 1
    assert result.provider == "fake"


# ---------------------------------------------------------------- the trace fields


async def test_trace_measurements_from_a_result():
    fields = trace_measurements(_second_result())

    assert fields == {
        "provider": "gemini",
        "latency_ms": 7,
        "attempts": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "estimated_cost_usd": 0.02,
    }


async def test_trace_measurements_from_a_measured_error():
    exc = LLMTimeoutError("deadline")
    exc.measurement = CallMeasurement(provider="gemini", latency_ms=180_000, attempts=2)

    assert trace_measurements(exc) == {
        "provider": "gemini",
        "latency_ms": 180_000,
        "attempts": 2,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_cost_usd": None,
    }


async def test_trace_measurements_from_an_unmeasured_error_or_nothing_are_all_unknown():
    unknown = dict.fromkeys(
        (
            "provider",
            "latency_ms",
            "attempts",
            "input_tokens",
            "output_tokens",
            "estimated_cost_usd",
        )
    )

    assert trace_measurements(RateLimitedError("429")) == unknown
    assert trace_measurements(None) == unknown


async def test_every_trace_measurement_is_a_parameter_of_the_trace_repository():
    parameters = inspect.signature(AgentTraceRepository.create).parameters

    assert set(trace_measurements(None)) <= set(parameters)
