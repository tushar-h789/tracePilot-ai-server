"""Logs query API schemas (Phase 4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Level = Literal["debug", "info", "warning", "error", "critical"]


class LogEventSummary(BaseModel):
    """A log row as listed (attributes omitted; fetch the detail endpoint for them)."""

    id: int
    event_id: uuid.UUID
    timestamp: datetime
    level: Level
    service_name: str
    environment: str
    message: str
    trace_id: str | None
    request_id: str | None
    http_method: str | None
    http_path: str | None
    http_status: int | None
    duration_ms: float | None
    is_synthetic: bool


class LogEventDetail(LogEventSummary):
    received_at: datetime
    attributes: dict[str, Any]


class LogListResponse(BaseModel):
    items: list[LogEventSummary]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next (older) page; null on the last page"
    )
    range_from: datetime = Field(description="Resolved inclusive start (UTC)")
    range_to: datetime = Field(description="Resolved exclusive end (UTC)")


class FacetValue(BaseModel):
    value: str
    count: int


class LogFacetsResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    levels: list[FacetValue]
    services: list[FacetValue] = Field(description="Top services by event count (max 100)")
    environments: list[FacetValue] = Field(description="Top environments by event count (max 20)")
