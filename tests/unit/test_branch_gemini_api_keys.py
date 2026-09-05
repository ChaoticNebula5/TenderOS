"""B16: confirms the right Gemini API key reaches the right specialist
branch's own GeminiClient construction - compliance_analysis, talent_
analysis, and commercial_analysis are the three nodes LangGraph's Send()
dispatches in parallel (orchestration/graph.py), the only three that ever
competed for the same free-tier per-minute quota window. See
KNOWN_LIMITATIONS.md's "Workflow retry controls are inert" entry for the
real live-run failure this fixes, and Settings.compliance_gemini_api_key's
own comment (core/config.py) for why extraction/drafting/red-team don't
need this (they run sequentially, never competing for the same window).

B17: GeminiClient construction itself moved to agents/llm/factory.py::
build_llm_client, the one real place this codebase now decides gemini vs
fake - each node's own _build_llm_client() still resolves ITS OWN key
first (unchanged) but delegates the actual construction there. So the spy
now patches factory.GeminiClient (the one real binding left), not three
separate per-module ones - proving the same thing this file always proved
(the right key reaches the right branch), just through the collapsed
choke point instead of three independent constructions.

Wiring only - no real LLM cost. Each test patches the single GeminiClient
binding with a spy that records its constructor kwargs, then calls the
real per-module _build_llm_client() and asserts on the captured api_key -
not a real network call, not FakeLLMClient (which implements LLMClient,
not GeminiClient's own __init__ signature).
"""

from tenderos_backend.agents.llm import factory
from tenderos_backend.core.config import Settings, settings
from tenderos_backend.orchestration.nodes import commercial, compliance, talent


class _SpyGeminiClient:
    last_api_key: str | None = None

    def __init__(self, api_key: str, redis_client: object, cache_ttl_seconds: int) -> None:
        _SpyGeminiClient.last_api_key = api_key


def test_compliance_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "compliance_gemini_api_key", "compliance-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    compliance._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "compliance-only-key"


def test_compliance_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "compliance_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    compliance._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_talent_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "talent_gemini_api_key", "talent-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    talent._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "talent-only-key"


def test_talent_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "talent_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    talent._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_commercial_branch_uses_its_own_key_when_set(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "commercial_gemini_api_key", "commercial-only-key")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    commercial._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "commercial-only-key"


def test_commercial_branch_falls_back_to_the_shared_key_when_unset(monkeypatch):
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "commercial_gemini_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "shared-key")

    commercial._build_llm_client()

    assert _SpyGeminiClient.last_api_key == "shared-key"


def test_the_three_branch_keys_are_independent_of_each_other(monkeypatch):
    """Not just "each falls back correctly in isolation" - the three
    fields don't alias one another. Sets all three to different real
    values in one test and confirms each node picks up only its own."""
    monkeypatch.setattr(factory, "GeminiClient", _SpyGeminiClient)
    # These 7 tests all assume build_llm_client() reaches the Gemini branch
    # at all - true only when settings.llm_provider == "gemini". Reading the
    # live singleton instead of pinning it here means this whole file passes
    # or fails depending on which command started the test process, not on
    # the per-branch-key logic it actually exercises: a fake-provider E2E
    # run sets LLM_PROVIDER=fake as a real OS env var, which build_llm_client
    # checks first and short-circuits on - _SpyGeminiClient never gets
    # constructed, so last_api_key silently holds a stale value from
    # whichever earlier test last set it, not a fresh failure.
    monkeypatch.setattr(settings, "llm_provider", "gemini")
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


def test_fake_provider_returns_the_shared_fake_client_regardless_of_branch(monkeypatch):
    # settings.llm_provider == "fake" short-circuits build_llm_client before
    # any per-branch key resolution or GeminiClient construction happens at
    # all - confirmed by NOT patching GeminiClient here (if the fake branch
    # were somehow skipped, this would fail loudly by trying to construct a
    # real GeminiClient with no real api_key, not silently pass).
    monkeypatch.setattr(settings, "llm_provider", "fake")

    result = compliance._build_llm_client()

    assert result is factory.fake_llm_client()


def test_default_provider_is_gemini_not_fake():
    # The one assertion that would catch a flipped default silently making
    # every real deployment run against fabricated output - but it has to
    # check the DECLARED default, not settings.llm_provider (the live
    # singleton), which reads whatever LLM_PROVIDER the process that started
    # this test suite actually set. That's deliberately "fake" during a
    # fake-provider E2E run, which would make this assertion's result depend
    # on which command launched the process instead of on whether the
    # field's own default is safe. model_fields is pydantic's declared-
    # schema view - it never touches the environment or any .env file.
    assert Settings.model_fields["llm_provider"].default == "gemini"
