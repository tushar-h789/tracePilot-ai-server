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

from app.core.config import Settings
from app.main import create_app

UNREACHABLE_DB = "postgresql+psycopg://tracepilot:unit-test@127.0.0.1:1/tracepilot_test"
UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"

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
