from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same field, same default, as apps/api/src/tenderos_api/config.py's own
    # app_env - duplicated here for the identical reason neo4j_uri/user/
    # password are (see that trio's own comment below): worker/orchestration
    # code imports THIS Settings class, not the API's thinner one.
    app_env: str = "local"

    postgres_dsn: str
    redis_url: str
    jwt_signing_key: str
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 7

    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"

    max_upload_size_mb: int = 50

    gemini_api_key: str = ""
    # B16: one Gemini API key per specialist branch that dispatches in
    # PARALLEL via LangGraph's Send() - compliance_analysis, talent_analysis,
    # commercial_analysis (orchestration/graph.py's own fan-out). A real live
    # run measured this fixture's own workload (85 real compliance calls
    # alone) blowing through the free tier's per-minute quota
    # (GenerateRequestsPerMinutePerProjectPerModel-FreeTier, 15/min for
    # gemini-3.5-flash-lite) when all three branches shared one key/project
    # and ran simultaneously - three separate Google accounts, each with its
    # own key, are the fix ONLY if each is also a genuinely separate GCP
    # project (a shared project would mean a shared quota, defeating the
    # whole point) - check the project ID on each key's own AI Studio/
    # console page before relying on this, not assumed equivalent to
    # "different account." Each field defaults to "" and falls back to the
    # shared gemini_api_key above at its own call site
    # (orchestration/nodes/compliance.py, talent.py, commercial.py) when
    # unset, so a deployment that never sets these keeps working exactly as
    # before - all three sharing one quota, same as it always has.
    # Extraction, drafting, and red-team deliberately stay on the shared
    # key: they run sequentially in the graph, never competing for the same
    # per-minute window the three parallel branches do.
    compliance_gemini_api_key: str = ""
    talent_gemini_api_key: str = ""
    commercial_gemini_api_key: str = ""

    # B17: which LLMClient implementation agents/llm/factory.py::
    # build_llm_client actually constructs. "gemini" (the real provider) is
    # the default and everywhere-except-explicit-opt-in value; "fake" makes
    # every one of the nine call sites this factory replaced (see that
    # module's own docstring) return FakeLLMClient instead - enabling
    # infrastructure for a deterministic, no-cost E2E run of the golden
    # path, not a feature for its own sake. Literal, not a plain str: an
    # invalid value (a typo) fails loudly at Settings() construction,
    # before anything runs, rather than silently falling through to
    # whichever branch an if/else happened to default to.
    llm_provider: Literal["gemini", "fake"] = "gemini"
    extractor_model: str = "changeme"
    verifier_model: str = "changeme"
    # A2.2's REASONING_MODEL alias - locked in the Build Guide's own env var
    # table (line 500) since B2, unused until now. PRD 25.5 names this role
    # explicitly: "strong reasoning model for complex compliance analysis" -
    # compliance proposal is exactly that call, so it uses this alias rather
    # than EXTRACTOR_MODEL/VERIFIER_MODEL (both tuned for B5's narrower,
    # cheaper extraction/verification calls) or a new alias of its own.
    reasoning_model: str = "changeme"
    # Redis-backed response cache TTL for the LLM adapter (temperature=0
    # calls are deterministic enough that re-running scripts/run_eval.py
    # while iterating shouldn't re-pay rate limit/cost for the same request).
    llm_cache_ttl_seconds: int = 86400

    # B6 retrieval. Named as required env vars in Build Guide A2 (line
    # 506-507) with no default value specified there - these starting
    # values are this project's own call (same "flag before deciding"
    # posture as B5's MAX_EXTRACTION_BATCH_TOKENS starting value), to be
    # confirmed/tuned against the real Recall@10 gate before it's treated
    # as final. retrieval_top_k: candidates fetched from EACH of lexical/
    # vector before RRF fusion. rerank_top_n: results returned after
    # reranking the fused candidates.
    retrieval_top_k: int = 20
    rerank_top_n: int = 10

    # B10: Neo4j projection. neo4j_uri/user/password/database mirror the
    # fields already proven working in apps/api/config.py's own health check
    # (unused there beyond connectivity) - duplicated here because
    # graph_projection/ imports from THIS Settings class, the one
    # orchestration nodes and domain services actually use, not the thin
    # API-layer one. Required (no default), same as postgres_dsn/redis_url/
    # s3_* above - Neo4j is a core infra dependency here, not an optional
    # one, so a missing .env entry should fail Settings() construction
    # loudly rather than silently connect to a wrong default.
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str = "neo4j"
    # D4's locked default. The code may configure up to 6 "for experiments"
    # via this same env var, but no API request is ever allowed to override
    # it per-call (schemas/graph.py's impact request body has no hop-count
    # field at all) - an unbounded or caller-controlled traversal depth on a
    # variable-length Cypher path is a denial-of-service shape, closed by
    # construction now rather than deferred to B15.
    graph_max_hops: int = 4

    # I5 (Build Guide :2755-2763): "Every workflow tracks estimated model
    # cost. When projected cost exceeds MAX_TENDER_COST_USD: pause, show
    # current progress, require bid-manager approval to continue." A spec
    # deviation now closed, not a feature added - this env var sat in
    # .env.example claiming a real cap for 16 blocks (deleted as dead
    # config in B16 once agent_traces.estimated_cost_usd was found to have
    # zero writers; see KNOWN_LIMITATIONS.md's dead-instrument entry),
    # while I5's own gate was never built at all, undetected the same way
    # preview_impact's own USED_IN gap and Part F's tool layer were:
    # spec names a real behaviour, code never builds it, nothing records
    # the deviation.
    #
    # 25.0, derived not guessed: this deployment's real configured models
    # (EXTRACTOR_MODEL/VERIFIER_MODEL/REASONING_MODEL) are all flash-lite
    # tier ($0.30/$2.50 per 1M tokens - agents/llm/gemini_adapter.py's own
    # price table), and every specialist/drafting/critique node calls
    # settings.reasoning_model. Using the one real measured call-volume
    # figure already on record here ("85 real compliance calls alone" in
    # one live run, see compliance_gemini_api_key's own comment above) and
    # typical per-call token sizes, a full run including up to
    # max_revisions=2 extra passes prices at roughly $0.30-$1.00 today -
    # this number is loose against that (mostly a backstop against a
    # genuine runaway/bug), and a starting value to be tuned, not a final
    # one, same posture already established for retrieval_top_k/
    # MAX_EXTRACTION_BATCH_TOKENS.
    max_tender_cost_usd: float = 25.0


def _reject_fake_provider_in_production(llm_provider: str, app_env: str) -> None:
    """B17's hard guard, not a preference: a public single-VM demo (Phase 4)
    silently serving fabricated LLM output under a real product's own name
    would be the worst possible place for this project's own "no inflated
    claims" standard (Part N line 3931) to fail. Called immediately below,
    at import time, right after settings is constructed - specifically so
    this fails before ANY process that imports this module (api, worker, a
    script) can do anything at all, not deferred to whenever the first LLM
    call happens to occur. A plain function, not an inline `if`, so this
    exact logic is directly unit-testable with arbitrary inputs, not only
    exercisable by actually setting real env vars and re-importing a
    module.

    This is one half of the real guard; the other half is structural, not
    code: Phase 4's own deployment compose/env file must never define
    LLM_PROVIDER at all (or must hardcode it to "gemini" with no override
    path) - this assertion is what catches the case where that discipline
    is forgotten or violated, not a substitute for it.
    """
    if llm_provider == "fake" and app_env == "production":
        raise RuntimeError(
            "LLM_PROVIDER=fake is not permitted when APP_ENV=production. "
            "Fake mode exists only for local/E2E reachability testing - a "
            "production deployment must never silently serve fabricated LLM "
            "output. Refusing to start."
        )


settings = Settings()
_reject_fake_provider_in_production(settings.llm_provider, settings.app_env)
