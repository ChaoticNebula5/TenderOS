import logging

from redis.asyncio import Redis as AsyncRedis

from tenderos_backend.agents.llm.client import LLMClient
from tenderos_backend.agents.llm.fake_client import FakeLLMClient
from tenderos_backend.agents.llm.gemini_adapter import GeminiClient
from tenderos_backend.core.config import settings

# B17: the one real place this codebase decides gemini vs fake - closing a
# real finding, not adding a nice-to-have. Before this, nine call sites
# (six orchestration/nodes/*.py, three apps/worker/jobs/*.py) each
# independently, hardcoded, constructed a real GeminiClient with no
# conditional branch at all - this project's own LLMClient Protocol is
# described as "provider-neutral," but the interface being neutral didn't
# mean the WIRING was: swapping providers meant nine edits, and a tenth
# call site copy-pasting the existing pattern would have silently gotten
# Gemini whether or not that was intended. A tenth call site that goes
# through this function instead inherits the real/fake switch for free.
#
# Fake mode needs a single, shared, pre-configurable FakeLLMClient
# instance, not a fresh one per call the way the real branch deliberately
# stays per-call (see build_llm_client's own docstring) - E2E setup has to
# script/set-fallback responses onto it BEFORE any job runs, and every
# call site across the whole golden path needs to see those same scripted
# responses, not nine independent empty instances. Real GeminiClient calls
# have the opposite need (a fresh client, cheap to construct, no shared
# state to leak between unrelated jobs) - the two branches are
# deliberately asymmetric, not an oversight.
_fake_client = FakeLLMClient()

logger = logging.getLogger(__name__)


def log_llm_provider() -> None:
    """Phase 4 plan item 46: one INFO line naming the provider this process
    will use, logged once at startup by the same three entrypoints that
    call install_fake_fallback_responses() (apps/api/main.py, apps/worker/
    worker.py, apps/worker/outbox_dispatcher.py). Before this nothing said
    which provider a container was on, so a stack that came up live from
    .env (2026-11-04) looked exactly like a fake one until its first call.
    The provider only, never a key.
    """
    if settings.llm_provider == "fake":
        logger.info("llm provider fake: every LLM call is answered by FakeLLMClient")
    else:
        logger.info(
            "llm provider %s: LLM calls go to the live provider and cost money",
            settings.llm_provider,
        )


def fake_llm_client() -> FakeLLMClient:
    """The one shared fake-mode instance build_llm_client returns - exposed
    so E2E setup code (and tests) can call .script(...)/.set_fallback(...)
    on the exact object every real call site will see, without needing its
    own reference threaded through nine modules."""
    return _fake_client


def build_llm_client(*, api_key: str | None = None) -> LLMClient:
    """Replaces all nine modules' own private _build_llm_client() bodies -
    each of those still exists (existing tests monkeypatch that exact
    per-module name; keeping it is what makes this refactor not also a
    27-call-site test rewrite), but each is now a one-line delegate to
    this function instead of its own independent GeminiClient construction.

    api_key: only meaningful for the real provider. Three call sites
    (compliance/talent/commercial analysis nodes) each have their own
    dedicated key (B16 - avoids three parallel Send()-dispatched branches
    competing for one key's per-minute rate limit) and pass their own
    already-resolved `settings.X_gemini_api_key or settings.gemini_api_key`
    value; every other call site leaves this None and gets the shared
    settings.gemini_api_key. Irrelevant in fake mode - FakeLLMClient takes
    no key at all.
    """
    if settings.llm_provider == "fake":
        return _fake_client
    redis_client = AsyncRedis.from_url(settings.redis_url)
    return GeminiClient(
        api_key=api_key or settings.gemini_api_key,
        redis_client=redis_client,
        cache_ttl_seconds=settings.llm_cache_ttl_seconds,
    )
