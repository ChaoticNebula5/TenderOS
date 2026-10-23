from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from tenderos_backend.core.config import reject_production_problems


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    postgres_dsn: str
    redis_url: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    s3_endpoint_url: str
    # Already in .env.example (B0), never read anywhere until now - the web
    # app is served on a different origin than the API in every
    # configuration this project has (Vite dev on 5173 vs uvicorn on 8000
    # locally; the same port split inside docker-compose), so the browser's
    # own CORS preflight blocks every request without this being wired up
    # somewhere. B13 is the first thing that actually calls the API from a
    # browser, which is why nothing caught this until now.
    web_base_url: str = "http://localhost:5173"

    # B15: on by default (secure by default) - the test suite explicitly
    # disables this (Makefile's own test target, RATE_LIMIT_ENABLED=false)
    # because both this project's real HTTP test harnesses
    # (fastapi.testclient.TestClient and httpx.ASGITransport) report a
    # single constant request.client.host for every request in the entire
    # session ("testclient" / "127.0.0.1" respectively, confirmed directly
    # during B15 - not assumed) - a global per-IP limit would eventually
    # trip across completely unrelated tests sharing that one identity,
    # not a real signal about the code under test. See
    # rate_limit.py's own module docstring for the real, dedicated tests
    # that verify the actual limiting logic with this flag deliberately
    # re-enabled.
    rate_limit_enabled: bool = True

    # B16: E2E test-harness bypass - see main.py's _rate_limit middleware
    # for the full contract. Off by default and gated behind an explicit
    # env var, not just the header main.py also checks: a request carrying
    # the bypass header changes nothing at all unless this flag is also
    # true, so there is no way to enable the bypass by only sending a
    # header - a real deployment that never sets this env var (nothing
    # does except docker-compose.e2e.yml's own explicit override) is
    # unaffected no matter what any client sends.
    e2e_rate_limit_bypass_enabled: bool = False


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _api_production_problems(s: Settings) -> list[str]:
    """Phase 4 item 5 (decision 0042), the API's half: what these settings
    get wrong for APP_ENV=production, one line per problem, naming the
    variable, never its value. Empty outside production. The backend
    settings (JWT key, model names, credentials) are checked when
    tenderos_backend.core.config is imported, which every API process does.
    """
    if s.app_env != "production":
        return []
    problems: list[str] = []
    origin = urlsplit(s.web_base_url)
    if origin.scheme != "https" or (origin.hostname or "") in _LOCAL_HOSTS:
        problems.append("WEB_BASE_URL (the CORS origin) must be an https URL that is not localhost")
    if not s.rate_limit_enabled:
        problems.append("RATE_LIMIT_ENABLED must be true")
    if s.e2e_rate_limit_bypass_enabled:
        problems.append("E2E_RATE_LIMIT_BYPASS_ENABLED must be false")
    return problems


settings = Settings()
reject_production_problems(_api_production_problems(settings))
