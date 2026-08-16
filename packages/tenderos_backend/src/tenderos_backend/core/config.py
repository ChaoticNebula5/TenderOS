from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # A2's locked choice is a provider-neutral LLMClient interface with model
    # IDs supplied purely as configuration - llm_provider selects which
    # agents/llm/*_adapter.py implementation gets constructed. Defaulting to
    # "gemini" here is this project's own choice (not the Build Guide's
    # literal "Anthropic-compatible adapter" example), flagged in the B5
    # commit message.
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
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


settings = Settings()
