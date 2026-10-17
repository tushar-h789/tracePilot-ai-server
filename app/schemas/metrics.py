"""Metrics API schemas (Phase 4). Definitions: docs/METRICS.md."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.logs import LogEventSummary

NullReason = Literal["no_requests", "no_latency_samples", "insufficient_samples"]


class Latency(BaseModel):
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    samples: int = Field(description="Requests with a duration_ms in range")
    min_samples: int = Field(description="Percentiles are null below this many samples")
    reason: NullReason | None = Field(description="Why the percentiles are null, if they are")


class Totals(BaseModel):
    events: int = Field(description="All accepted log events in range")
    requests: int = Field(description="Events with an http_status")
    errors: int = Field(description="Events with level error/critical OR http_status >= 500")
    request_errors: int = Field(description="Requests that are errors (numerator of error_rate)")
    error_rate: float | None = Field(description="request_errors / requests; null if no requests")
    error_rate_reason: NullReason | None
    latency: Latency


class ProjectOverviewResponse(BaseModel):
    project_id: uuid.UUID
    range_from: datetime
    range_to: datetime
    totals: Totals


class TimeseriesPoint(BaseModel):
    bucket_start: datetime
    events: int
    requests: int
    errors: int
    request_errors: int
    samples: int
    p95_ms: float | None = Field(description="Null when the bucket has fewer than min_samples")


class TimeseriesResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    bucket_seconds: int
    min_samples: int
    points: list[TimeseriesPoint]


class ServiceSummary(BaseModel):
    service_name: str
    events: int
    requests: int
    errors: int
    request_errors: int
    error_rate: float | None
    samples: int
    p95_ms: float | None


class ServicesResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    min_samples: int
    items: list[ServiceSummary] = Field(description="Top services by event count (max 50)")


class RecentErrorsResponse(BaseModel):
    range_from: datetime
    range_to: datetime
    items: list[LogEventSummary]


class ProjectSummary(BaseModel):
    project_id: uuid.UUID
    name: str
    slug: str
    environment: str
    events: int
    requests: int
    errors: int
    request_errors: int
    error_rate: float | None
    samples: int
    p95_ms: float | None


class OrgRecentError(LogEventSummary):
    project_id: uuid.UUID
    project_name: str


class OrgSummaryResponse(BaseModel):
    org_id: uuid.UUID
    range_from: datetime
    range_to: datetime
    project_count: int = Field(description="Active (non-archived) projects included")
    totals: Totals
    min_samples: int
    projects: list[ProjectSummary]
    recent_errors: list[OrgRecentError]
