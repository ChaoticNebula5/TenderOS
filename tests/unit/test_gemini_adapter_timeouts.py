"""Phase 4: GeminiClient's two timeouts (agents/llm/gemini_adapter.py).

- every provider HTTP request is bounded (HttpOptions.timeout, 60 s), and
  the httpx.TimeoutException google-genai re-raises becomes LLMTimeoutError;
- one logical call, retries and backoffs included, is bounded by the call
  deadline (180 s), and running past it is LLMTimeoutError too.

No network and no real LLM cost: the SDK's generate_content is replaced by
a stand-in, and Redis by a cache that is always empty. The api key is fake.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel
from tenderos_backend.agents.llm import gemini_adapter
from tenderos_backend.agents.llm.client import LLMTimeoutError
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient

pytestmark = pytest.mark.asyncio


class _Answer(BaseModel):
    text: str


class _EmptyCache:
    async def get(self, key: str) -> None:
        return None

    async def set(self, *args, **kwargs) -> None:
        return None


def _client_whose_provider(generate_content) -> GeminiClient:
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


async def test_the_sdk_client_is_built_with_the_http_timeout(monkeypatch):
    captured: dict = {}

    def spy_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(gemini_adapter.genai, "Client", spy_client)
    GeminiClient(api_key="fake-key", redis_client=_EmptyCache(), cache_ttl_seconds=60)

    # google-genai takes the timeout in milliseconds.
    assert captured["http_options"].timeout == 60_000


async def test_a_provider_request_timeout_becomes_llm_timeout_error():
    calls = 0

    async def hangs_up(**kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("fake read timeout")

    with pytest.raises(LLMTimeoutError):
        await _ask(_client_whose_provider(hangs_up))
    # Not retried: a request that already waited the full 60 s is not
    # retried inside the same call.
    assert calls == 1


async def test_a_call_past_its_deadline_becomes_llm_timeout_error(monkeypatch):
    monkeypatch.setattr(gemini_adapter, "_CALL_DEADLINE_SECONDS", 0.05)

    async def never_answers(**kwargs):
        await asyncio.sleep(10)

    with pytest.raises(LLMTimeoutError, match="call deadline"):
        await asyncio.wait_for(_ask(_client_whose_provider(never_answers)), timeout=5)
