"""Application settings loaded from environment variables (and an optional local `.env`).

Settings are validated at startup; invalid configuration fails fast with a clear message that
never echoes secret values (see `load_settings`).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, HttpUrl, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Markers that identify placeholder credentials copied from `.env.example`.
INSECURE_MARKERS = ("change-me", "changeme")


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class ConfigError(RuntimeError):
    """Raised when settings are invalid. The message lists fields and reasons, never values."""


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "TracePilot AI"
    app_env: AppEnv = AppEnv.DEV
    log_level: LogLevel = "INFO"

    # Connection strings contain credentials -> SecretStr (masked in repr / logs).
    database_url: SecretStr
    redis_url: SecretStr

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3100", "http://127.0.0.1:3100"]
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)
    db_statement_timeout_ms: int = Field(default=5000, ge=100, le=600_000)
    health_check_timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    ai_provider: Literal["ollama", "fake"] = "ollama"
    ollama_base_url: HttpUrl = HttpUrl("http://ollama:11434")
    ollama_model: str = "qwen2.5:3b"

    celery_task_always_eager: bool = False

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _parse_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        if parts.scheme != "postgresql+psycopg":
            raise ValueError("must use the 'postgresql+psycopg://' scheme (SQLAlchemy + psycopg 3)")
        if not parts.hostname or not parts.path.strip("/"):
            raise ValueError("must include a host and a database name")
        return value

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        if parts.scheme not in {"redis", "rediss"}:
            raise ValueError("must use the 'redis://' or 'rediss://' scheme")
        if not parts.hostname:
            raise ValueError("must include a host")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin == "*":
                raise ValueError("wildcard '*' is not allowed; list explicit origins")
            parts = urlsplit(origin)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.netloc
                or parts.path not in {"", "/"}
            ):
                raise ValueError("each origin must look like 'http(s)://host[:port]'")
        return [o.rstrip("/") for o in value]

    @field_validator("allowed_hosts")
    @classmethod
    def _validate_hosts(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("at least one host is required")
        return value

    @model_validator(mode="after")
    def _refuse_insecure_prod(self) -> Settings:
        if self.app_env is not AppEnv.PROD:
            return self
        problems: list[str] = []
        for name in ("database_url", "redis_url"):
            secret = getattr(self, name).get_secret_value().lower()
            if any(marker in secret for marker in INSECURE_MARKERS):
                problems.append(f"{name} contains a placeholder credential")
        if any(host == "*" for host in self.allowed_hosts):
            problems.append("allowed_hosts must not contain '*' in prod")
        if self.celery_task_always_eager:
            problems.append("celery_task_always_eager must be false in prod")
        if self.log_level == "DEBUG":
            problems.append("log_level DEBUG is not allowed in prod")
        if problems:
            raise ValueError("insecure production configuration: " + "; ".join(problems))
        return self

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.PROD

    @property
    def docs_enabled(self) -> bool:
        """Interactive docs and the OpenAPI document are not served in prod (SECURITY T14)."""
        return not self.is_prod


def format_validation_error(exc: ValidationError) -> str:
    """Render a ValidationError without input values (they may contain secrets)."""
    lines = []
    for err in exc.errors(include_input=False, include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "settings"
        lines.append(f"  - {loc.upper()}: {err['msg']}")
    return "Invalid configuration:\n" + "\n".join(lines)


def load_settings(**overrides: object) -> Settings:
    try:
        return Settings(**overrides)  # type: ignore[arg-type]
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
