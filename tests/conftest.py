"""Shared fixtures.

Unit tests need no services. Integration tests (marker `integration`) use real Postgres/Redis
from TEST_DATABASE_URL / TEST_REDIS_URL; they are skipped when those are unset, unless
TP_REQUIRE_INTEGRATION=1 (set by CI and scripts/ci-local.sh) in which case they fail instead.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

# Module-level singletons (e.g. the Celery app) read settings at import time. Environment
# variables take precedence over a developer's local .env, keeping tests hermetic.
os.environ["APP_ENV"] = "test"
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://tracepilot:unit-test@127.0.0.1:1/tracepilot_test"
)
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:1/0")
os.environ.setdefault("SECRET_KEY", "unit-test-secret-key-not-used-anywhere-else-0123456789")

from app.core.config import Settings
from app.main import create_app

UNREACHABLE_DB = "postgresql+psycopg://tracepilot:unit-test@127.0.0.1:1/tracepilot_test"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"
TEST_SECRET_KEY = "unit-test-secret-key-not-used-anywhere-else-0123456789"

SettingsFactory = Callable[..., Settings]


@pytest.fixture
def make_settings() -> SettingsFactory:
    def factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "_env_file": None,
            "app_env": "test",
            "database_url": UNREACHABLE_DB,
            "redis_url": UNREACHABLE_REDIS,
            "health_check_timeout_seconds": 0.5,
            "db_connect_timeout_seconds": 1,
            "secret_key": TEST_SECRET_KEY,
            # Cheap Argon2id parameters keep the suite fast; production defaults are tested
            # separately in test_passwords.py.
            "argon2_time_cost": 1,
            "argon2_memory_cost_kib": 1024,
            "argon2_parallelism": 1,
        }
        values.update(overrides)
        return Settings(**values)

    return factory


def _client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(settings), base_url="http://localhost", raise_server_exceptions=False
    ) as client:
        yield client


@pytest.fixture
def client(make_settings: SettingsFactory) -> Iterator[TestClient]:
    """App wired to unreachable dependencies (nothing listens on port 1)."""
    yield from _client(make_settings())


def _integration_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        message = f"{name} is not set; integration test needs real Postgres/Redis"
        if os.environ.get("TP_REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return value


@pytest.fixture
def test_database_url() -> str:
    url = _integration_env("TEST_DATABASE_URL")
    # Guard: migration tests downgrade to base, so never point them at a non-test database.
    if not url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database whose name ends with '_test'")
    return url


@pytest.fixture
def test_redis_url() -> str:
    return _integration_env("TEST_REDIS_URL")


@pytest.fixture
def integration_client(
    make_settings: SettingsFactory, test_database_url: str, test_redis_url: str
) -> Iterator[TestClient]:
    yield from _client(make_settings(database_url=test_database_url, redis_url=test_redis_url))


# --- Phase 2: identity / org integration fixtures ------------------------------------------

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TABLES = "audit_logs, organization_members, sessions, organizations, users"


@pytest.fixture(scope="session")
def migrated_database_url() -> str:
    """Upgrade the test database to head once per session (needs real Postgres)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        message = "TEST_DATABASE_URL is not set; integration test needs real Postgres/Redis"
        if os.environ.get("TP_REQUIRE_INTEGRATION") == "1":
            pytest.fail(message)
        pytest.skip(message)
    if not url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        pytest.fail("TEST_DATABASE_URL must point to a database whose name ends with '_test'")
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.attributes["skip_logging_config"] = True
    cfg.attributes["database_url"] = url
    command.upgrade(cfg, "head")
    return url


@pytest.fixture
def clean_state(migrated_database_url: str, test_redis_url: str) -> None:
    """Empty identity tables and rate-limit keys before each test."""
    from redis import Redis
    from sqlalchemy import create_engine, pool, text

    engine = create_engine(migrated_database_url, poolclass=pool.NullPool)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    engine.dispose()
    redis_client = Redis.from_url(test_redis_url)
    for key in redis_client.scan_iter(match="rl:*"):
        redis_client.delete(key)
    redis_client.close()


@pytest.fixture
def make_app(
    make_settings: SettingsFactory,
    migrated_database_url: str,
    test_redis_url: str,
    clean_state: None,
) -> Callable[..., Any]:
    from fastapi import FastAPI

    created: list[FastAPI] = []

    def factory(**overrides: Any) -> FastAPI:
        values: dict[str, Any] = {
            "database_url": migrated_database_url,
            "redis_url": test_redis_url,
            **overrides,
        }
        app = create_app(make_settings(**values))
        created.append(app)
        return app

    return factory


@pytest.fixture
def app(make_app: Callable[..., Any]) -> Any:
    return make_app()


@pytest.fixture
def db_session(app: Any) -> Iterator[Any]:
    """A separate SQLAlchemy session on the same test database (for assertions/setup)."""
    session = app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
