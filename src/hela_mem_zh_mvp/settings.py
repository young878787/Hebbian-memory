"""Single, secret-safe runtime configuration boundary."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

EXPECTED_DATABASE = "hebbian_memory_mvp"


class Settings(BaseSettings):
    """Loads shell variables first, then the repository-root `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    google_api_key: SecretStr | None = None
    google_model: str = "gemini-3.1-flash-lite"
    reasoning_model: str = "0"
    google_thinking_level: str = "minimal"

    embedding_base_url: str = "http://localhost:8001/v1"
    embedding_model: str = "Qwen3-Embedding-4B"
    embedding_api_key: SecretStr = SecretStr("local")

    postgres_host: str = "192.168.137.2"
    postgres_port: int = 5432
    postgres_db: str = EXPECTED_DATABASE
    postgres_user: str = "hebbian_memory_app"
    postgres_password: SecretStr | None = None

    @field_validator("embedding_base_url")
    @classmethod
    def validate_v1_endpoint(cls, value: str) -> str:
        if not value.rstrip("/").endswith("/v1"):
            raise ValueError("EMBEDDING_BASE_URL must end with /v1")
        return value.rstrip("/")

    def missing(self, *, include_google: bool = False) -> list[str]:
        missing: list[str] = []
        if not Path(".env").is_file():
            missing.append(".env")
        if not self.postgres_password or not self.postgres_password.get_secret_value():
            missing.append("POSTGRES_PASSWORD")
        if include_google and (
            not self.google_api_key or not self.google_api_key.get_secret_value()
        ):
            missing.append("GOOGLE_API_KEY")
        return missing

    def database_url(self) -> URL:
        if self.postgres_db != EXPECTED_DATABASE:
            raise ValueError(f"POSTGRES_DB must be {EXPECTED_DATABASE!r}")
        if not self.postgres_password or not self.postgres_password.get_secret_value():
            raise ValueError("POSTGRES_PASSWORD is required")
        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
