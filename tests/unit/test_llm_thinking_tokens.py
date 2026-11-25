"""PR 29 (Q5 b): thinking tokens are recorded on their own and priced as
output.

Google's pricing page (aistudio.google.com/docs/pricing, read 2026-11-21)
labels each model's output row "Output price (including thinking tokens)":
the tokens a model spends thinking are billed at its output rate. So:
- `thinking_tokens` is the SDK's `usage_metadata.thoughts_token_count`;
- `output_tokens` stays the answer's tokens only (`candidates_token_count`);
- `estimated_cost_usd` prices output and thinking tokens at the output rate.

No network and no real LLM cost: the SDK's generate_content is replaced by a
stand-in and Redis by an in-memory cache; the api key is fake.
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel
from tenderos_backend.agents.llm import gemini_adapter
from tenderos_backend.agents.llm.client import (
    CallMeasurement,
    LLMResult,
    MalformedOutputError,
    call_with_repair,
    trace_measurements,
)
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient, _estimate_cost_usd

pytestmark = pytest.mark.asyncio

# A model id these tests put in gemini_adapter's price table, at (1.50, 9.00)
# USD per 1M tokens (PR 34: the real table no longer has gemini-3.5-flash).
_PRICED_MODEL = "test-priced-model"


@pytest.fixture(autouse=True)
def _priced_model(monkeypatch):
    monkeypatch.setitem(gemini_adapter._PRICE_PER_MILLION_TOKENS_USD, _PRICED_MODEL, (1.50, 9.00))


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


def _response(text: str, prompt: int, answer: int, thoughts: int | None):
    return SimpleNamespace(
        text=text,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt,
            candidates_token_count=answer,
            thoughts_token_count=thoughts,
        ),
    )


async def _ask(client, model: str = _PRICED_MODEL):
    return await client.complete_structured(
        agent_name="fake_agent",
        model=model,
        system_prompt="system",
        user_prompt="user",
        response_schema=_Answer,
    )


async def test_thinking_tokens_are_recorded_apart_from_the_answer_and_priced_as_output():
    async def thinks(**kwargs):
        return _response('{"text": "ok"}', prompt=1000, answer=200, thoughts=800)

    result = await _ask(_gemini(thinks))

    assert result.input_tokens == 1000
    assert result.output_tokens == 200, "output_tokens is the answer only"
    assert result.thinking_tokens == 800
    # 1000 in at 1.50, and 200 + 800 out at 9.00, per 1M tokens.
    assert result.estimated_cost_usd == pytest.approx(0.0015 + 0.0090)


async def test_a_response_without_thinking_records_zero_thinking_tokens():
    # The SDK leaves thoughts_token_count unset when the model did not think.
    async def answers(**kwargs):
        return _response('{"text": "ok"}', prompt=1000, answer=200, thoughts=None)

    result = await _ask(_gemini(answers))

    assert result.thinking_tokens == 0
    assert result.estimated_cost_usd == pytest.approx(0.0015 + 0.0018)


async def test_a_malformed_answer_keeps_its_thinking_tokens_and_their_cost():
    async def thinks_wrongly(**kwargs):
        return _response('{"unexpected": 1}', prompt=400, answer=100, thoughts=300)

    with pytest.raises(MalformedOutputError) as caught:
        await _ask(_gemini(thinks_wrongly))

    measurement = caught.value.measurement
    assert measurement is not None
    assert measurement.thinking_tokens == 300
    assert measurement.output_tokens == 100
    assert measurement.estimated_cost_usd == pytest.approx(0.0006 + 0.0036)


async def test_a_cache_hit_counts_no_thinking_tokens():
    async def must_not_be_called(**kwargs):
        raise AssertionError("a cache hit must not reach the provider")

    result = await _ask(_gemini(must_not_be_called, cache=_Cache(hit='{"text": "cached"}')))

    assert result.thinking_tokens is None
    assert result.estimated_cost_usd == 0.0


async def test_the_fake_never_thinks():
    fake = FakeLLMClient()
    fake.script("fake_agent", _Answer, [_Answer(text="ok")])

    result = await _ask(fake, model="any-model")

    assert result.thinking_tokens == 0


async def test_the_price_function_prices_thinking_as_output():
    assert _estimate_cost_usd(_PRICED_MODEL, 1000, 200, 800) == pytest.approx(0.0105)
    assert _estimate_cost_usd(_PRICED_MODEL, 1000, 200, None) == pytest.approx(0.0033)
    assert _estimate_cost_usd("not-in-the-table", 1000, 200, 800) is None


class _RepairStub:
    def __init__(self) -> None:
        self.calls = 0

    async def complete_structured(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            exc = MalformedOutputError("schema mismatch")
            exc.measurement = CallMeasurement(
                provider="gemini",
                latency_ms=5,
                attempts=1,
                input_tokens=100,
                output_tokens=20,
                estimated_cost_usd=0.01,
                thinking_tokens=50,
            )
            raise exc
        return LLMResult(
            parsed=_Answer(text="repaired"),
            provider="gemini",
            model=_PRICED_MODEL,
            input_tokens=120,
            output_tokens=30,
            latency_ms=7,
            estimated_cost_usd=0.02,
            attempts=1,
            thinking_tokens=70,
        )


async def test_a_repaired_call_adds_up_both_requests_thinking_tokens():
    result = await call_with_repair(
        _RepairStub(),
        agent_name="fake_agent",
        model=_PRICED_MODEL,
        system_prompt="system",
        user_prompt="user",
        response_schema=_Answer,
    )

    assert result.thinking_tokens == 120


async def test_trace_measurements_carries_thinking_tokens():
    exc = MalformedOutputError("bad")
    exc.measurement = CallMeasurement(
        provider="gemini", latency_ms=9, attempts=1, thinking_tokens=42
    )

    assert trace_measurements(exc)["thinking_tokens"] == 42
    assert trace_measurements(None)["thinking_tokens"] is None
