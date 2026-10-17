import pytest
from pydantic import ValidationError

from app.core.config import AppEnv, ConfigError, Settings, load_settings
from tests.conftest import SettingsFactory

SECRET = "S3cr3t-Value-Should-Not-Leak"


def test_valid_settings_and_defaults(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    assert settings.app_env is AppEnv.TEST
    assert settings.ai_provider == "ollama"
    assert settings.docs_enabled is True
    assert "http://localhost:3100" in settings.cors_origins


def test_csv_lists_are_parsed(make_settings: SettingsFactory) -> None:
    settings = make_settings(
        cors_origins="http://localhost:3100, https://app.example.test/",
        allowed_hosts="localhost,api",
    )
    assert settings.cors_origins == ["http://localhost:3100", "https://app.example.test"]
    assert settings.allowed_hosts == ["localhost", "api"]


def test_csv_lists_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test:3100,http://b.test:3100")
    monkeypatch.setenv("ALLOWED_HOSTS", "a.test")
    settings = Settings(_env_file=None)
    assert settings.cors_origins == ["http://a.test:3100", "http://b.test:3100"]
    assert settings.allowed_hosts == ["a.test"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_env", "staging"),
        ("log_level", "VERBOSE"),
        ("database_url", f"postgresql://user:{SECRET}@db/app"),
        ("database_url", f"mysql+pymysql://user:{SECRET}@db/app"),
        ("database_url", "postgresql+psycopg://user@db"),
        ("redis_url", f"http://:{SECRET}@redis:6379/0"),
        ("cors_origins", "*"),
        ("cors_origins", "localhost:3100"),
        ("ai_provider", "openai"),
        ("health_check_timeout_seconds", 0),
    ],
)
def test_invalid_values_fail_fast(
    make_settings: SettingsFactory, field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_config_error_names_field_but_never_echoes_value() -> None:
    with pytest.raises(ConfigError) as info:
        load_settings(
            _env_file=None,
            database_url=f"mysql://user:{SECRET}@db/app",
            redis_url="redis://localhost:6379/0",
        )
    message = str(info.value)
    assert "DATABASE_URL" in message
    assert "postgresql+psycopg" in message
    assert SECRET not in message


def test_missing_required_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with pytest.raises(ConfigError) as info:
        load_settings(_env_file=None)
    assert "DATABASE_URL" in str(info.value)
    assert "REDIS_URL" in str(info.value)


def test_secrets_are_masked_in_repr(make_settings: SettingsFactory) -> None:
    settings = make_settings(database_url=f"postgresql+psycopg://u:{SECRET}@db:5432/app_test")
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings.model_dump())


def test_prod_refuses_placeholder_credentials(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError) as info:
        make_settings(
            app_env="prod",
            database_url="postgresql+psycopg://tp:change-me-local-only@db:5432/tp",
        )
    assert "placeholder credential" in str(info.value)


@pytest.mark.parametrize(
    "overrides",
    [
        {"allowed_hosts": "*"},
        {"celery_task_always_eager": True},
        {"log_level": "DEBUG"},
        {"cookie_secure": False},
        {"argon2_memory_cost_kib": 1024},
        {"secret_key": "change-me-please-change-me-please-0123456789"},
    ],
)
def test_prod_refuses_insecure_options(
    make_settings: SettingsFactory, overrides: dict[str, object]
) -> None:
    values: dict[str, object] = {**PROD_OK, **overrides}
    with pytest.raises(ValidationError):
        make_settings(app_env="prod", **values)


PROD_OK: dict[str, object] = {
    "allowed_hosts": "api.tracepilot.test",
    "cookie_secure": True,
    "argon2_memory_cost_kib": 65536,
}


def test_prod_with_real_values_disables_docs(make_settings: SettingsFactory) -> None:
    settings = make_settings(app_env="prod", **PROD_OK)
    assert settings.is_prod
    assert settings.docs_enabled is False
    assert settings.session_cookie_name == "__Host-tp_session"
    assert settings.csrf_cookie_name == "__Host-tp_csrf"


def test_dev_cookie_names_have_no_host_prefix(make_settings: SettingsFactory) -> None:
    settings = make_settings()
    assert settings.session_cookie_name == "tp_session"
    assert settings.csrf_cookie_name == "tp_csrf"


def test_secret_key_is_required_and_long(make_settings: SettingsFactory) -> None:
    with pytest.raises(ValidationError):
        make_settings(secret_key="short")


def test_trusted_proxies_are_validated(make_settings: SettingsFactory) -> None:
    assert make_settings(trusted_proxies="10.0.0.0/8, 127.0.0.1").trusted_proxies == [
        "10.0.0.0/8",
        "127.0.0.1",
    ]
    with pytest.raises(ValidationError):
        make_settings(trusted_proxies="not-an-ip")
