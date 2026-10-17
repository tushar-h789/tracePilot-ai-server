"""Application settings loaded from environment variables (and an optional local `.env`).

Settings are validated at startup; invalid configuration fails fast with a clear message that
never echoes secret values (see `load_settings`).
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_network
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

    # --- Identity / sessions (Phase 2, ADR-018) ---------------------------------------------
    # Server-side secret for HMAC-signed CSRF tokens. >= 32 chars; prod refuses placeholders.
    secret_key: SecretStr = Field(min_length=32)
    # Cookies get `Secure` and the `__Host-` name prefix when true (required in prod / HTTPS).
    cookie_secure: bool = False
    session_idle_timeout_minutes: int = Field(default=60, ge=5, le=24 * 60)
    session_absolute_timeout_hours: int = Field(default=24, ge=1, le=24 * 30)
    # `last_seen_at` is written at most this often per session (bounds DB writes).
    session_touch_interval_seconds: int = Field(default=60, ge=0, le=3600)
    max_sessions_listed: int = Field(default=50, ge=1, le=500)

    # Argon2id cost (argon2-cffi defaults = RFC 9106 low-memory profile: t=3, m=64 MiB, p=4).
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65536, ge=1024, le=262_144)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    # Fixed-window rate limits backed by Redis (requests per window).
    login_rate_limit_per_email: int = Field(default=5, ge=1, le=1000)
    login_rate_limit_per_ip: int = Field(default=20, ge=1, le=10_000)
    login_rate_limit_window_seconds: int = Field(default=60, ge=1, le=86_400)
    register_rate_limit_per_ip: int = Field(default=20, ge=1, le=10_000)
    register_rate_limit_window_seconds: int = Field(default=600, ge=1, le=86_400)
    # Peers (IPs/CIDRs) whose X-Forwarded-For is trusted for the client IP (e.g. the web proxy).
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # --- Phase 3: projects, ingestion keys, ingestion ------------------------------------------
    # HMAC-SHA256 pepper for ingestion key hashes. Separate from SECRET_KEY so rotating the CSRF
    # signing key does not invalidate every ingestion key (ADR-021).
    ingest_key_pepper: SecretStr = Field(min_length=32)
    max_active_keys_per_project: int = Field(default=10, ge=1, le=100)
    key_rotation_max_grace_seconds: int = Field(default=86_400, ge=0, le=7 * 86_400)
    ingest_max_body_bytes: int = Field(default=1_048_576, ge=1024, le=10 * 1_048_576)
    ingest_max_batch_events: int = Field(default=500, ge=1, le=1000)
    ingest_max_past_seconds: int = Field(default=7 * 86_400, ge=60, le=30 * 86_400)
    ingest_max_future_seconds: int = Field(default=300, ge=0, le=86_400)
    ingest_rate_limit_requests_per_key: int = Field(default=120, ge=1, le=100_000)
    ingest_rate_limit_events_per_project: int = Field(default=50_000, ge=1, le=10_000_000)
    ingest_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    ingest_key_touch_interval_seconds: int = Field(default=60, ge=0, le=3600)

    @field_validator("cors_origins", "allowed_hosts", "trusted_proxies", mode="before")
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
        secret_key = self.secret_key.get_secret_value().lower()
        if any(marker in secret_key for marker in INSECURE_MARKERS):
            problems.append("secret_key contains a placeholder value")
        pepper = self.ingest_key_pepper.get_secret_value().lower()
        if any(marker in pepper for marker in INSECURE_MARKERS):
            problems.append("ingest_key_pepper contains a placeholder value")
        if not self.cookie_secure:
            problems.append("cookie_secure must be true in prod (HTTPS)")
        if self.argon2_memory_cost_kib < 19_456:
            problems.append("argon2_memory_cost_kib must be >= 19456 (OWASP minimum) in prod")
        if problems:
            raise ValueError("insecure production configuration: " + "; ".join(problems))
        return self

    @property
    def is_prod(self) -> bool:
        return self.app_env is AppEnv.PROD

    @field_validator("trusted_proxies")
    @classmethod
    def _validate_proxies(cls, value: list[str]) -> list[str]:
        for item in value:
            ip_network(item, strict=False)  # raises ValueError for garbage
        return value

    @property
    def session_cookie_name(self) -> str:
        # `__Host-` requires Secure + Path=/ + no Domain, so it is only used over HTTPS.
        return "__Host-tp_session" if self.cookie_secure else "tp_session"

    @property
    def csrf_cookie_name(self) -> str:
        return "__Host-tp_csrf" if self.cookie_secure else "tp_csrf"

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
