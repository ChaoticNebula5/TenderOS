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


settings = Settings()
