from pydantic_settings import BaseSettings, SettingsConfigDict


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


settings = Settings()
