"""FastAPI application factory.

Run locally:  uv run uvicorn app.main:create_app --factory --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from redis import Redis

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.security import install_exception_handlers, install_http_middleware
from app.db.session import build_engine, build_sessionmaker

API_PREFIX = "/api/v1"
VERSION = "0.1.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    engine = build_engine(settings)
    redis_client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        redis_client.close()
        engine.dispose()

    docs = settings.docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=VERSION,
        openapi_url=f"{API_PREFIX}/openapi.json" if docs else None,
        docs_url=f"{API_PREFIX}/docs" if docs else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_sessionmaker(engine)
    app.state.redis = redis_client

    # Order: last added runs first. Host check -> CORS -> request context/security headers.
    install_http_middleware(app, settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    install_exception_handlers(app, settings)

    app.include_router(api_router, prefix=API_PREFIX)
    return app
