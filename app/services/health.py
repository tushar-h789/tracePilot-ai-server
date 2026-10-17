"""Readiness checks for the API's hard dependencies (Postgres, Redis)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis import Redis
from sqlalchemy import Engine

from app.repositories.health import ping_database, ping_redis
from app.schemas.health import DependencyCheck, ReadinessChecks, ReadinessResponse

logger = logging.getLogger("app.health")


def _run_check(name: str, probe: Callable[[], None]) -> DependencyCheck:
    started = time.perf_counter()
    try:
        probe()
        status = "ok"
    except Exception as exc:  # any failure => not ready; details stay in the server log
        logger.warning("readiness_check_failed", extra={"check": name, "error": type(exc).__name__})
        status = "error"
    latency = round((time.perf_counter() - started) * 1000, 2)
    return DependencyCheck(status=status, latency_ms=latency)  # type: ignore[arg-type]


class HealthService:
    def __init__(self, engine: Engine, redis_client: Redis) -> None:
        self._engine = engine
        self._redis = redis_client

    def readiness(self) -> ReadinessResponse:
        checks = ReadinessChecks(
            database=_run_check("database", lambda: ping_database(self._engine)),
            redis=_run_check("redis", lambda: ping_redis(self._redis)),
        )
        ok = checks.database.status == "ok" and checks.redis.status == "ok"
        return ReadinessResponse(status="ok" if ok else "unavailable", checks=checks)
