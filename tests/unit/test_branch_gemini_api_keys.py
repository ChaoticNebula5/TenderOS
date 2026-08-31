"""B16: confirms the right Gemini API key reaches the right specialist
branch's own GeminiClient construction - compliance_analysis, talent_
analysis, and commercial_analysis are the three nodes LangGraph's Send()
dispatches in parallel (orchestration/graph.py), the only three that ever
competed for the same free-tier per-minute quota window. See
KNOWN_LIMITATIONS.md's "Workflow retry controls are inert" entry for the
real live-run failure this fixes, and Settings.compliance_gemini_api_key's
own comment (core/config.py) for why extraction/drafting/red-team don't
need this (they run sequentially, never competing for the same window).

Wiring only - no real LLM cost. Each test monkeypatches the target module's
own GeminiClient binding (a direct `from ... import GeminiClient`, so each
module has its own separate name to intercept) with a spy that records its
constructor kwargs, then calls the real _build_llm_client() and asserts on
the captured api_key - not a real network call, not FakeLLMClient (which
implements LLMClient, not GeminiClient's own __init__ signature).
"""

from tenderos_backend.core.config import settings
from tenderos_backend.orchestration.nodes import commercial, compliance, talent


class _SpyGeminiClient:
    last_api_key: str | None = None

    def __init__(self, api_key: str, redis_client: object, cache_ttl_seconds: int) -> None:
        _SpyGeminiClient.last_api_key = api_key


def test_compliance_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(compliance, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "compliance_gemini_api_key", "compliance-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    compliance._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "compliance-only-key"


def test_compliance_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(compliance, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "compliance_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    compliance._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_talent_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(talent, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "talent_gemini_api_key", "talent-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    talent._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "talent-only-key"


def test_talent_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(talent, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "talent_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    talent._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_commercial_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(commercial, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "commercial_gemini_api_key", "commercial-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    commercial._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "commercial-only-key"


def test_commercial_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(commercial, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "commercial_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    commercial._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_the_three_branch_keys_are_independent_of_each_other(monkeypatch):
    """Not just "each falls back correctly in isolation" - the three
    fields don't alias one another. Sets all three to different real
    values in one test and confirms each node picks up only its own."""
    monkeypatch.setattr(compliance, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(talent, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(commercial, "GeminiClient", _SpyGeminiClient)
    monkeypatch.setattr(settings, "compliance_gemini_api_key", "key-a")
    monkeypatch.setattr(settings, "talent_gemini_api_key", "key-b")
    monkeypatch.setattr(settings, "commercial_gemini_api_key", "key-c")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    compliance._build_llm_client()
    assert _SpyGeminiClient.last_api_key == "key-a"

    talent._build_llm_client()
    assert _SpyGeminiClient.last_api_key == "key-b"

    commercial._build_llm_client()
    assert _SpyGeminiClient.last_api_key == "key-c"
