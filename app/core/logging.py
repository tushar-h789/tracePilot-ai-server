"""Structured JSON logging (stdlib only).

- One JSON object per line on stdout.
- `request_id` from a context variable is attached to every record.
- Extra fields whose key looks sensitive are replaced with "[REDACTED]"; request bodies,
  cookies and Authorization headers are never logged by this codebase.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "session",
    "csrf",
    "credential",
    "password_hash",
    "private_key",
    "database_url",
    "redis_url",
    "ingest_key",
    "ingestion_key",
    "bearer",
    "plaintext",
)
# Value-level scrubbing (defence in depth): a full ingestion key that ends up inside a log
# message or string field keeps only its public prefix.
INGEST_KEY_VALUE_RE = re.compile(r"\b(tp_[0-9a-f]{12})_[A-Za-z0-9_-]{43}")
REDACTED = "[REDACTED]"

# Attributes present on every LogRecord; anything else came from `extra=`.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_secrets(text: str) -> str:
    """Replace full ingestion keys inside free text with `<prefix>_[REDACTED]`."""
    if "tp_" not in text:
        return text
    return INGEST_KEY_VALUE_RE.sub(r"\1_[REDACTED]", text)


def redact(value: Any, key: str = "") -> Any:
    if key and _is_sensitive(key):
        return REDACTED
    if isinstance(value, str):
        return scrub_secrets(value)
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": scrub_secrets(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = redact(value, key)
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["exc"] = scrub_secrets(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Route third-party loggers through the root JSON handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "alembic"):
        lib_logger = logging.getLogger(name)
        lib_logger.handlers.clear()
        lib_logger.propagate = True
    # SQL echo would log statements/parameters; keep it quiet unless debugging locally.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
