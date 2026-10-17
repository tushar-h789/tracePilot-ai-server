"""Celery application (worker + beat share this module).

celery -A app.workers.celery_app worker --loglevel=INFO --concurrency=1
celery -A app.workers.celery_app beat --loglevel=INFO
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("tracepilot", include=["app.workers.tasks"])
    app.conf.update(
        broker_url=settings.redis_url.get_secret_value(),
        # Task outcomes are persisted in Postgres rows by the tasks themselves (ADR-007),
        # so no Celery result backend is configured.
        result_backend=None,
        task_ignore_result=True,
        task_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        broker_connection_retry_on_startup=True,
        task_default_queue="default",
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=True,
        task_soft_time_limit=60,
        task_time_limit=90,
        # Placeholder: detection (~60 s) and retention (daily) schedules are added in P5/P7.
        beat_schedule={},
    )
    return app


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    configure_logging(get_settings().log_level)


celery_app = create_celery()
