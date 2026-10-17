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
from app.core.passwords import PasswordService
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
    app.state.passwords = PasswordService(settings)

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
    install_openapi_extras(app)
    return app


def install_openapi_extras(app: FastAPI) -> None:
    """Add what FastAPI cannot infer: the raw-body ingest schema and the key security scheme.

    The ingest route reads the body itself (size cap before parsing), so `IngestBatch` is
    registered here to keep it in the published contract and the generated client types.
    """
    from typing import Any

    from fastapi.openapi.utils import get_openapi

    from app.schemas.ingest import IngestBatch

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        batch = IngestBatch.model_json_schema(ref_template="#/components/schemas/{model}")
        for name, definition in batch.pop("$defs", {}).items():
            schemas.setdefault(name, definition)
        schemas["IngestBatch"] = batch
        components["securitySchemes"] = {
            "IngestionKey": {
                "type": "http",
                "scheme": "bearer",
                "description": "Project ingestion key: `Authorization: Bearer tp_<id>_<secret>`",
            }
        }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
