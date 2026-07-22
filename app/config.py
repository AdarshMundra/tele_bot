from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unknown env vars (e.g. CRON_SECRET, DEFAULT_CURRENCY)
    )

    telegram_bot_token: str
    telegram_webhook_secret: str
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"

    # .env uses DATABASE_URL
    database_url: str
    # .env uses DEFAULT_TIMEZONE
    default_timezone: str = "Asia/Kolkata"

    allowed_telegram_user_id: int

    @property
    def supabase_db_url(self) -> str:
        return self.database_url

    @property
    def timezone(self) -> str:
        return self.default_timezone


settings = Settings()
