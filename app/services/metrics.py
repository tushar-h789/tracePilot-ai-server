"""Project and organization metrics computed from log_events (definitions: docs/METRICS.md).

Null semantics: a metric is null - never 0 or a guess - when its denominator is empty or there
are too few latency samples; `reason` says which.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.time_range import TimeRange, bucket_step, resolve_range
from app.repositories.log_queries import LogQueryRepository
from app.repositories.projects import ProjectRepository
from app.schemas.metrics import (
    Latency,
    NullReason,
    OrgRecentError,
    OrgSummaryResponse,
    ProjectOverviewResponse,
    ProjectSummary,
    RecentErrorsResponse,
    ServicesResponse,
    ServiceSummary,
    TimeseriesPoint,
    TimeseriesResponse,
    Totals,
)
from app.services.logs import summary_from_row
from app.services.organizations import OrgScope
from app.services.projects import ProjectScope

MIN_LATENCY_SAMPLES = 20  # overview, services, projects
MIN_BUCKET_SAMPLES = 5  # per time-series bucket
SERVICES_LIMIT = 50
ORG_PROJECTS_LIMIT = 200
RECENT_ERRORS_DEFAULT = 10


def error_rate(requests: int, request_errors: int) -> tuple[float | None, NullReason | None]:
    if requests == 0:
        return None, "no_requests"
    return request_errors / requests, None


def _pct(values: Sequence[float] | None, index: int) -> float | None:
    if not values:
        return None
    value = values[index]
    return None if value is None else round(float(value), 3)


def latency(row: dict[str, Any], min_samples: int = MIN_LATENCY_SAMPLES) -> Latency:
    samples = int(row["samples"])
    reason: NullReason | None = None
    if samples == 0:
        reason = "no_latency_samples"
    elif samples < min_samples:
        reason = "insufficient_samples"
    pcts = None if reason else row["pcts"]
    return Latency(
        p50_ms=_pct(pcts, 0),
        p95_ms=_pct(pcts, 1),
        p99_ms=_pct(pcts, 2),
        samples=samples,
        min_samples=min_samples,
        reason=reason,
    )


def totals(row: dict[str, Any]) -> Totals:
    requests, request_errors = int(row["requests"]), int(row["request_errors"])
    rate, reason = error_rate(requests, request_errors)
    return Totals(
        events=int(row["events"]),
        requests=requests,
        errors=int(row["errors"]),
        request_errors=request_errors,
        error_rate=rate,
        error_rate_reason=reason,
        latency=latency(row),
    )


EMPTY_ROW: dict[str, Any] = {
    "events": 0,
    "requests": 0,
    "errors": 0,
    "request_errors": 0,
    "samples": 0,
    "pcts": None,
}


class MetricsService:
    def __init__(self, db: Session) -> None:
        self.repo = LogQueryRepository(db)
        self.projects = ProjectRepository(db)

    # --- project ------------------------------------------------------------------------------
    def overview(
        self, scope: ProjectScope, start: datetime | None, end: datetime | None
    ) -> ProjectOverviewResponse:
        rng = resolve_range(start, end)
        return ProjectOverviewResponse(
            project_id=scope.project_id,
            range_from=rng.start,
            range_to=rng.end,
            totals=totals(self.repo.totals([scope.project_id], rng)),
        )

    def _timeseries(self, ids: Sequence[uuid.UUID], rng: TimeRange) -> TimeseriesResponse:
        step = bucket_step(rng.span)
        points = []
        for r in self.repo.timeseries(ids, rng, step):
            samples = int(r["samples"])
            p95 = r["pcts"][0] if samples >= MIN_BUCKET_SAMPLES and r["pcts"] else None
            points.append(
                TimeseriesPoint(
                    bucket_start=r["bucket"],
                    events=int(r["events"]),
                    requests=int(r["requests"]),
                    errors=int(r["errors"]),
                    request_errors=int(r["request_errors"]),
                    samples=samples,
                    p95_ms=None if p95 is None else round(float(p95), 3),
                )
            )
        return TimeseriesResponse(
            range_from=rng.start,
            range_to=rng.end,
            bucket_seconds=int(step.total_seconds()),
            min_samples=MIN_BUCKET_SAMPLES,
            points=points,
        )

    def timeseries(
        self, scope: ProjectScope, start: datetime | None, end: datetime | None
    ) -> TimeseriesResponse:
        return self._timeseries([scope.project_id], resolve_range(start, end))

    def services(
        self, scope: ProjectScope, start: datetime | None, end: datetime | None
    ) -> ServicesResponse:
        rng = resolve_range(start, end)
        items = []
        for r in self.repo.per_service(scope.project_id, rng, SERVICES_LIMIT):
            rate, _ = error_rate(int(r["requests"]), int(r["request_errors"]))
            lat = latency(r)
            items.append(
                ServiceSummary(
                    service_name=r["service"],
                    events=int(r["events"]),
                    requests=int(r["requests"]),
                    errors=int(r["errors"]),
                    request_errors=int(r["request_errors"]),
                    error_rate=rate,
                    samples=lat.samples,
                    p95_ms=lat.p95_ms,
                )
            )
        return ServicesResponse(
            range_from=rng.start, range_to=rng.end, min_samples=MIN_LATENCY_SAMPLES, items=items
        )

    def recent_errors(
        self, scope: ProjectScope, start: datetime | None, end: datetime | None, limit: int
    ) -> RecentErrorsResponse:
        rng = resolve_range(start, end)
        rows = self.repo.recent_errors([scope.project_id], rng, limit)
        return RecentErrorsResponse(
            range_from=rng.start, range_to=rng.end, items=[summary_from_row(r) for r in rows]
        )

    # --- organization -------------------------------------------------------------------------
    def org_summary(
        self, scope: OrgScope, start: datetime | None, end: datetime | None, recent: int
    ) -> OrgSummaryResponse:
        rng = resolve_range(start, end)
        projects = self.projects.list_active(scope.org_id, ORG_PROJECTS_LIMIT)
        ids = [p.id for p in projects]
        if ids:
            total_row = self.repo.totals(ids, rng)
            by_project = {r["project_id"]: r for r in self.repo.per_project(ids, rng)}
            recent_rows = self.repo.recent_errors(ids, rng, recent)
        else:
            total_row, by_project, recent_rows = EMPTY_ROW, {}, []
        summaries = []
        for p in projects:
            r = by_project.get(p.id, EMPTY_ROW)
            rate, _ = error_rate(int(r["requests"]), int(r["request_errors"]))
            lat = latency(r)
            summaries.append(
                ProjectSummary(
                    project_id=p.id,
                    name=p.name,
                    slug=p.slug,
                    environment=p.environment,
                    events=int(r["events"]),
                    requests=int(r["requests"]),
                    errors=int(r["errors"]),
                    request_errors=int(r["request_errors"]),
                    error_rate=rate,
                    samples=lat.samples,
                    p95_ms=lat.p95_ms,
                )
            )
        summaries.sort(key=lambda s: (-s.events, s.name))
        return OrgSummaryResponse(
            org_id=scope.org_id,
            range_from=rng.start,
            range_to=rng.end,
            project_count=len(projects),
            totals=totals(total_row),
            min_samples=MIN_LATENCY_SAMPLES,
            projects=summaries,
            recent_errors=[
                OrgRecentError(
                    **summary_from_row(r).model_dump(),
                    project_id=r.project_id,
                    project_name=r.project_name,
                )
                for r in recent_rows
            ],
        )

    def org_timeseries(
        self, scope: OrgScope, start: datetime | None, end: datetime | None
    ) -> TimeseriesResponse:
        rng = resolve_range(start, end)
        ids = [p.id for p in self.projects.list_active(scope.org_id, ORG_PROJECTS_LIMIT)]
        if not ids:
            ids = [uuid.UUID(int=0)]  # zero-filled series, no rows can match
        return self._timeseries(ids, rng)
