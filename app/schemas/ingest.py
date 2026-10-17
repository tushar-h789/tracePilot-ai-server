"""Public log-ingestion contract (POST /api/v1/ingest/logs).

Strict: unknown fields (including any `project_id`) are rejected; the project comes only from
the ingestion key. The whole batch is validated before anything is stored (all-or-nothing).
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from app.schemas.common import StrictModel

MAX_MESSAGE_CHARS = 8192
MAX_ATTRIBUTES_BYTES = 16_384
MAX_ATTRIBUTES_DEPTH = 5
MAX_ATTRIBUTES_KEYS = 128
MAX_BATCH_HARD_LIMIT = 1000  # schema guard; the configured limit (default 500) is enforced too

Level = Literal["debug", "info", "warning", "error", "critical"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


def _walk(value: Any, depth: int, counter: list[int]) -> None:
    if depth > MAX_ATTRIBUTES_DEPTH:
        raise ValueError(f"attributes nesting must not exceed {MAX_ATTRIBUTES_DEPTH} levels")
    if isinstance(value, dict):
        counter[0] += len(value)
        if counter[0] > MAX_ATTRIBUTES_KEYS:
            raise ValueError(f"attributes must not contain more than {MAX_ATTRIBUTES_KEYS} keys")
        for key, item in value.items():
            if len(key) > 128:
                raise ValueError("attribute keys must be at most 128 characters")
            if "\x00" in key:
                raise ValueError("attributes must not contain NUL characters")
            _walk(item, depth + 1, counter)
    elif isinstance(value, list):
        for item in value:
            _walk(item, depth + 1, counter)
    elif isinstance(value, str) and "\x00" in value:
        raise ValueError("attributes must not contain NUL characters")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("attribute numbers must be finite")


class LogEventIn(StrictModel):
    event_id: uuid.UUID = Field(
        description="Client-generated UUID; the idempotency key per project"
    )
    timestamp: AwareDatetime = Field(
        description="When the event happened; must include a timezone (stored as UTC)"
    )
    level: Level
    service_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")
    environment: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$",
        description="Defaults to the project's environment",
    )
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    trace_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    request_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000, allow_inf_nan=False)
    http_method: HttpMethod | None = None
    http_path: str | None = Field(default=None, min_length=1, max_length=512)
    http_status: int | None = Field(default=None, ge=100, le=599)
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            f"JSON object: depth ≤ {MAX_ATTRIBUTES_DEPTH}, ≤ {MAX_ATTRIBUTES_KEYS} keys, "
            f"≤ {MAX_ATTRIBUTES_BYTES} bytes serialized. Sensitive-looking keys are redacted."
        ),
    )

    @field_validator("attributes")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        _walk(value, 1, [0])
        size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
        if size > MAX_ATTRIBUTES_BYTES:
            raise ValueError(f"attributes must be at most {MAX_ATTRIBUTES_BYTES} bytes")
        return value

    @field_validator("message", "http_path")
    @classmethod
    def _no_nul(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("must not contain NUL characters")
        return value


class IngestBatch(StrictModel):
    events: list[LogEventIn] = Field(min_length=1, max_length=MAX_BATCH_HARD_LIMIT)


class IngestResult(BaseModel):
    received: int = Field(description="Events in the request")
    accepted: int = Field(description="Newly stored events")
    duplicates: int = Field(description="Events whose event_id was already stored for the project")
    rejected: int = Field(
        description="Always 0 on success: any invalid event rejects the whole batch with 422"
    )
