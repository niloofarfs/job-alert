from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str
    app_env: str = "development"
    log_level: str = "INFO"

    poll_interval_seconds: int = Field(default=600, ge=30)
    company_concurrency: int = Field(default=8, ge=1, le=32)
    miss_threshold: int = Field(default=2, ge=1, le=10)

    http_timeout_seconds: float = Field(default=30.0, gt=0)
    http_connect_timeout_seconds: float = Field(default=10.0, gt=0)
    http_max_retries: int = Field(default=3, ge=0, le=8)
    http_user_agent: str = "JobSearchAlert/1.0 (self-hosted job monitor)"

    matching_config_path: Path = Path("config/matching.yaml")
    companies_config_path: Path = Path("config/companies.yaml")

    notifications_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("DATABASE_URL is required")
        return value.strip()

    @field_validator("telegram_bot_token", "telegram_chat_id")
    @classmethod
    def empty_optional_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def telegram_required_when_enabled(self) -> "Settings":
        if self.notifications_enabled and (
            not self.telegram_bot_token or not self.telegram_chat_id
        ):
            raise ValueError(
                "NOTIFICATIONS_ENABLED is true but TELEGRAM_BOT_TOKEN or "
                "TELEGRAM_CHAT_ID is missing"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration:\n{exc}") from exc
