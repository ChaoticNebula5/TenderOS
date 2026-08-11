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
    # Redis-backed response cache TTL for the LLM adapter (temperature=0
    # calls are deterministic enough that re-running scripts/run_eval.py
    # while iterating shouldn't re-pay rate limit/cost for the same request).
    llm_cache_ttl_seconds: int = 86400


settings = Settings()
