from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_dsn: str
    jwt_signing_key: str
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 7


settings = Settings()
