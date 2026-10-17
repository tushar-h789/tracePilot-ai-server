"""Shared FastAPI dependencies (DI). Auth/scope dependencies are added in Phase 2."""

from fastapi import Request

from app.core.config import Settings
from app.services.health import HealthService


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_health_service(request: Request) -> HealthService:
    return HealthService(engine=request.app.state.engine, redis_client=request.app.state.redis)
