import logging

from app.workers.celery_app import celery_app

logger = logging.getLogger("app.workers")


@celery_app.task(name="tracepilot.ping")
def ping() -> str:
    """Trivial task used to verify broker connectivity and worker liveness."""
    logger.info("ping_task_executed")
    return "pong"
