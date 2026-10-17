from collections.abc import Iterator

import pytest

from app.workers.celery_app import celery_app, create_celery
from app.workers.tasks import ping
from tests.conftest import SettingsFactory


@pytest.fixture
def eager() -> Iterator[None]:
    previous = celery_app.conf.task_always_eager
    celery_app.conf.task_always_eager = True
    try:
        yield
    finally:
        celery_app.conf.task_always_eager = previous


@pytest.mark.usefixtures("eager")
def test_ping_task_runs_eagerly() -> None:
    assert ping.delay().get(timeout=5) == "pong"


def test_celery_config_is_safe(make_settings: SettingsFactory) -> None:
    app = create_celery(make_settings(celery_task_always_eager=True))
    assert app.conf.task_serializer == "json"
    assert app.conf.accept_content == ["json"]  # no pickle
    assert app.conf.task_always_eager is True
    assert app.conf.beat_schedule == {}
    assert app.conf.broker_url == "redis://127.0.0.1:1/0"
    assert "tracepilot.ping" in celery_app.tasks
