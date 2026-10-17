"""HTTP hardening: request ids, security headers, generic error responses.

Authentication/authorization arrive in Phase 2; nothing here authenticates requests.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.logging import request_id_var

logger = logging.getLogger("app.http")

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_DOCS_PATH_RE = re.compile(r"^/api/v1/(docs|redoc)")

BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def security_headers(settings: Settings, path: str) -> dict[str, str]:
    headers = dict(BASE_SECURITY_HEADERS)
    # Swagger UI / ReDoc load assets from a CDN; the JSON API gets a deny-all CSP.
    if not _DOCS_PATH_RE.match(path):
        headers["Content-Security-Policy"] = API_CSP
    if settings.is_prod:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


def _incoming_request_id(request: Request) -> str:
    candidate = request.headers.get(REQUEST_ID_HEADER, "")
    return candidate if _REQUEST_ID_RE.match(candidate) else uuid.uuid4().hex


def install_http_middleware(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _incoming_request_id(request)
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers.update(security_headers(settings, request.url.path))
            response.headers[REQUEST_ID_HEADER] = request_id
            # Path only: query strings may carry sensitive values and are never logged.
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)


def install_exception_handlers(app: FastAPI, settings: Settings) -> None:
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
        # Full details go to the server log only; responses never include internals.
        logger.error(
            "unhandled_exception",
            exc_info=exc,
            extra={"path": request.url.path, "method": request.method, "request_id": request_id},
        )
        headers = security_headers(settings, request.url.path)
        headers[REQUEST_ID_HEADER] = request_id
        return JSONResponse(
            status_code=500,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Internal Server Error",
                "status": 500,
                "detail": "An unexpected error occurred.",
                "request_id": request_id,
            },
            headers=headers,
        )

    app.add_exception_handler(Exception, unhandled)
