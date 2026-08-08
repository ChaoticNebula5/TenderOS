from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_dsn: str
    jwt_signing_key: str
    jwt_access_minutes: int = 15
    jwt_refresh_days: int = 7

    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"

    max_upload_size_mb: int = 50


settings = Settings()
