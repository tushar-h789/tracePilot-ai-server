"""Event models mirroring the server's ingestion contract (POST /api/v1/ingest/logs)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Level = Literal["debug", "info", "warning", "error", "critical"]
HttpMethod = Literal["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]

MAX_BATCH_EVENTS = 500
MAX_MESSAGE_CHARS = 8192


def _now() -> datetime:
    return datetime.now(UTC)


class LogEvent(BaseModel):
    """One structured log event.

    `event_id` is the idempotency key: re-sending an event with the same id to the same project
    is counted as a duplicate and not stored twice, which makes retries safe.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: AwareDatetime = Field(default_factory=_now)
    level: Level = "info"
    service_name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:/-]+$")
    environment: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$"
    )
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    trace_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    request_id: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    duration_ms: float | None = Field(default=None, ge=0, le=86_400_000, allow_inf_nan=False)
    http_method: HttpMethod | None = None
    http_path: str | None = Field(default=None, min_length=1, max_length=512)
    http_status: int | None = Field(default=None, ge=100, le=599)
    attributes: dict[str, Any] = Field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class IngestResult(BaseModel):
    """Totals over every batch sent by one `send()` call."""

    received: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    batches: int = 0

    def add(self, other: dict[str, Any]) -> None:
        self.received += int(other.get("received", 0))
        self.accepted += int(other.get("accepted", 0))
        self.duplicates += int(other.get("duplicates", 0))
        self.rejected += int(other.get("rejected", 0))
        self.batches += 1
