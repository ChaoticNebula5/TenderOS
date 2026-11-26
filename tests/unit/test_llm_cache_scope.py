"""PR 35: the Gemini response cache is per organization, and its key covers
everything that shapes an answer.

- One organization's cached answer never serves another's call, and a
  client with no organization (only scripts that build GeminiClient
  themselves) has a namespace of its own.
- The organization id is in the clear in the key, outside the hash.
- The schema part is a hash of the JSON schema serialised with sorted keys:
  equal schemas hash the same, in this process and in another one; a
  changed field does not.
- Every generation setting the request sends is in the hash, read off the
  very config the request carries.
- A cached answer that no longer validates is a miss, not a failed call.
- The factory hands the organization to the client, and no app code builds
  GeminiClient anywhere else (the guard).

No network and no real LLM cost: the Gemini SDK's generate_content is a
counting stand-in, Redis an in-memory dict, and the api key is fake.
"""

import ast
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types as genai_types
from pydantic import BaseModel, create_model
from tenderos_backend.agents.llm import factory, gemini_adapter
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.agents.schemas.compliance import ComplianceProposal
from tenderos_backend.core.config import settings

pytestmark = pytest.mark.asyncio

_REPO = Path(__file__).resolve().parents[2]
_ORG_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
_ORG_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")


class _Answer(BaseModel):
    text: str


class _Store:
    """An in-memory stand-in for the Redis calls the adapter makes."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class _Provider:
    """Counts provider requests and keeps the config each one carried."""

    def __init__(self, answer: str = '{"text": "fresh"}') -> None:
        self.answer = answer
        self.configs: list[genai_types.GenerateContentConfig] = []

    async def generate_content(self, *, model, contents, config):
        self.configs.append(config)
        return SimpleNamespace(
            text=self.answer,
            usage_metadata=SimpleNamespace(
                prompt_token_count=10, candidates_token_count=2, thoughts_token_count=None
            ),
        )

    @property
    def requests(self) -> int:
        return len(self.configs)


def _gemini(store: _Store, provider: _Provider, organization_id: uuid.UUID | None) -> GeminiClient:
    client = GeminiClient(
        api_key=str(uuid.uuid4()),  # never a real key; the provider is a stand-in
        redis_client=store,
        cache_ttl_seconds=60,
        organization_id=organization_id,
    )
    client._client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=provider.generate_content))
    )
    return client


async def _ask(client: GeminiClient, schema: type[BaseModel] = _Answer, user: str = "user"):
    return await client.complete_structured(
        agent_name="fake_agent",
        model="test-model",
        system_prompt="system",
        user_prompt=user,
        response_schema=schema,
    )


def _same_name_schema(field_type: type) -> type[BaseModel]:
    """A schema class named `Answer`, defined afresh on each call: two calls
    with the same field type are two equal versions of one schema; a
    different type is that schema with a changed field."""
    return create_model("Answer", text=(field_type, ...))


# ------------------------------------------------------- between organizations


async def test_two_organizations_never_share_an_entry():
    store, provider = _Store(), _Provider()
    client_a = _gemini(store, provider, _ORG_A)
    client_b = _gemini(store, provider, _ORG_B)

    first = await _ask(client_a)
    second = await _ask(client_b)
    again = await _ask(client_a)

    assert provider.requests == 2
    assert first.attempts == 1
    assert second.attempts == 1
    assert again.attempts == 0


async def test_each_key_names_its_organization_in_the_clear():
    store, provider = _Store(), _Provider()
    await _ask(_gemini(store, provider, _ORG_A))
    await _ask(_gemini(store, provider, _ORG_B))

    keys = sorted(store.data)
    assert len(keys) == 2
    assert keys[0].startswith(f"llm_cache:v2:org:{_ORG_A}:")
    assert keys[1].startswith(f"llm_cache:v2:org:{_ORG_B}:")


@pytest.mark.parametrize("first_org", [None, _ORG_A])
async def test_no_organization_and_an_organization_never_answer_each_other(first_org):
    store, provider = _Store(), _Provider()
    second_org = _ORG_A if first_org is None else None

    await _ask(_gemini(store, provider, first_org))
    result = await _ask(_gemini(store, provider, second_org))

    assert provider.requests == 2
    assert result.attempts == 1
    assert any(key.startswith("llm_cache:v2:none:") for key in store.data)


# ------------------------------------------------------------------ the schema


async def test_equal_schemas_hash_the_same_and_a_changed_field_does_not():
    first = gemini_adapter._schema_hash(_same_name_schema(str))
    equal = gemini_adapter._schema_hash(_same_name_schema(str))
    changed = gemini_adapter._schema_hash(_same_name_schema(int))

    assert first == equal
    assert first != changed


async def test_json_schemas_differing_only_in_key_order_hash_the_same():
    # Pydantic happens to emit equal schemas in one order; the hash must not
    # rely on that (a later pydantic, or a schema built another way, may not).
    class _Forward(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            return {"title": "Answer", "type": "object", "properties": {"a": 1, "b": 2}}

    class _Backward(BaseModel):
        @classmethod
        def model_json_schema(cls, *args, **kwargs):
            return {"properties": {"b": 2, "a": 1}, "type": "object", "title": "Answer"}

    assert gemini_adapter._schema_hash(_Forward) == gemini_adapter._schema_hash(_Backward)


async def test_the_schema_hash_is_the_same_in_another_process():
    # A real agent schema, hashed here and in two fresh interpreters with
    # different hash seeds: the key must not depend on the process.
    code = (
        "from tenderos_backend.agents.llm.gemini_adapter import _schema_hash\n"
        "from tenderos_backend.agents.schemas.compliance import ComplianceProposal\n"
        "print(_schema_hash(ComplianceProposal))\n"
    )
    here = gemini_adapter._schema_hash(ComplianceProposal)
    for seed in ("1", "2"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == here


async def test_a_schema_changed_under_the_same_name_misses():
    store, provider = _Store(), _Provider(answer='{"text": "7"}')
    client = _gemini(store, provider, _ORG_A)

    await _ask(client, schema=_same_name_schema(str))
    await _ask(client, schema=_same_name_schema(str))
    assert provider.requests == 1

    await _ask(client, schema=_same_name_schema(int))
    assert provider.requests == 2


# ------------------------------------------------------- generation settings


async def test_the_request_carries_the_config_the_key_hashed():
    store, provider = _Store(), _Provider()

    await _ask(_gemini(store, provider, _ORG_A))

    sent = provider.configs[0]
    assert sent.temperature == 0
    assert sent.system_instruction == "system"
    assert sent.response_schema is _Answer
    settings_part = gemini_adapter._generation_settings(sent)
    assert '"temperature":0.0' in settings_part
    assert "system_instruction" not in settings_part
    assert "response_schema" not in settings_part


@pytest.mark.parametrize(
    "extra",
    [
        {"temperature": 0.5},
        {"max_output_tokens": 256},
        {"thinking_config": genai_types.ThinkingConfig(thinking_budget=0)},
        {
            "safety_settings": [
                genai_types.SafetySetting(
                    category=genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                )
            ]
        },
    ],
    ids=["temperature", "max_output_tokens", "thinking", "safety"],
)
async def test_a_changed_generation_setting_misses(monkeypatch, extra):
    store, provider = _Store(), _Provider()
    client = _gemini(store, provider, _ORG_A)
    await _ask(client)

    original = gemini_adapter._generation_config

    def changed(system_prompt, response_schema):
        return original(system_prompt, response_schema).model_copy(update=extra)

    monkeypatch.setattr(gemini_adapter, "_generation_config", changed)
    await _ask(client)

    assert provider.requests == 2


# ------------------------------------------------------- a stale cached answer


async def test_a_cached_answer_that_no_longer_validates_is_a_miss(caplog):
    store, provider = _Store(), _Provider()
    client = _gemini(store, provider, _ORG_A)
    await _ask(client)
    (key,) = store.data
    store.data[key] = '{"stale_cached_text": "from an older schema"}'

    with caplog.at_level(logging.WARNING, logger=gemini_adapter.__name__):
        result = await _ask(client)

    assert provider.requests == 2
    assert result.attempts == 1
    assert result.parsed == _Answer(text="fresh")
    assert store.data[key] == '{"text": "fresh"}'
    assert "a cached answer did not match _Answer" in caplog.text
    assert "stale_cached_text" not in caplog.text


async def test_a_stale_entry_is_gone_even_when_the_provider_then_fails():
    store = _Store()
    client = _gemini(store, _Provider(), _ORG_A)
    await _ask(client)
    (key,) = store.data
    store.data[key] = '{"stale_cached_text": "from an older schema"}'

    async def _unavailable(*, model, contents, config):
        raise RuntimeError("provider down")

    client._client.aio.models.generate_content = _unavailable
    with pytest.raises(RuntimeError):
        await _ask(client)

    assert key not in store.data


# ----------------------------------------------------- the factory, the guard


async def test_the_factory_gives_the_client_its_organization(monkeypatch):
    built: dict[str, object] = {}

    class _Spy:
        def __init__(self, **kwargs) -> None:
            built.update(kwargs)

    monkeypatch.setattr(factory, "GeminiClient", _Spy)
    monkeypatch.setattr(settings, "llm_provider", "gemini")

    factory.build_llm_client(organization_id=_ORG_B)

    assert built["organization_id"] == _ORG_B


async def test_no_app_code_builds_gemini_client_outside_the_factory():
    # The factory requires an organization (mypy refuses a call without
    # one); a GeminiClient built anywhere else in the app could skip it.
    # Scripts (scripts/) are outside the app and keep their own namespace.
    allowed = _REPO / "packages/tenderos_backend/src/tenderos_backend/agents/llm/factory.py"
    found = []
    roots = sorted((_REPO / "packages").glob("*/src")) + sorted((_REPO / "apps").glob("*/src"))
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "GeminiClient"
                ):
                    found.append((path, node.lineno))
    assert found, "the guard found no GeminiClient construction at all"
    assert [(p, n) for p, n in found if p != allowed] == []
