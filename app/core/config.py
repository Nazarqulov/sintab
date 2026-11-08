from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    owner_telegram_id: int
    database_url: str

    samsara_api_token: str | None = None
    samsara_webhook_secret: str | None = None

    default_language: str = "uz"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    telegram_webhook_secret_path: str = "telegram-webhook"
    log_level: str = "INFO"
    env: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
