"""Metrics routes (session auth; any member). Definitions: docs/METRICS.md."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import (
    MetricsServiceDep,
    require_csrf,
    require_org_permission,
    require_project_permission,
)
from app.api.v1.routes.logs import From, To
from app.core.permissions import Permission
from app.schemas.common import problem_responses
from app.schemas.metrics import (
    OrgSummaryResponse,
    ProjectOverviewResponse,
    RecentErrorsResponse,
    ServicesResponse,
    TimeseriesResponse,
)
from app.services.metrics import RECENT_ERRORS_DEFAULT
from app.services.organizations import OrgScope
from app.services.projects import ProjectScope

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_csrf)])

ProjectRead = Annotated[ProjectScope, Depends(require_project_permission(Permission.PROJECT_READ))]
OrgRead = Annotated[OrgScope, Depends(require_org_permission(Permission.ORG_READ))]
RecentLimit = Annotated[int, Query(ge=1, le=50)]
RESPONSES = problem_responses(401, 404, 422)


@router.get(
    "/projects/{project_id}/metrics/overview",
    response_model=ProjectOverviewResponse,
    summary="Totals, error rate and latency percentiles for a project",
    responses=RESPONSES,
)
def project_overview(
    scope: ProjectRead, service: MetricsServiceDep, start: From = None, end: To = None
) -> ProjectOverviewResponse:
    return service.overview(scope, start, end)


@router.get(
    "/projects/{project_id}/metrics/timeseries",
    response_model=TimeseriesResponse,
    summary="Zero-filled time buckets of events, requests, errors and p95",
    responses=RESPONSES,
)
def project_timeseries(
    scope: ProjectRead, service: MetricsServiceDep, start: From = None, end: To = None
) -> TimeseriesResponse:
    return service.timeseries(scope, start, end)


@router.get(
    "/projects/{project_id}/metrics/services",
    response_model=ServicesResponse,
    summary="Per-service requests, errors, error rate and p95",
    responses=RESPONSES,
)
def project_services(
    scope: ProjectRead, service: MetricsServiceDep, start: From = None, end: To = None
) -> ServicesResponse:
    return service.services(scope, start, end)


@router.get(
    "/projects/{project_id}/metrics/recent-errors",
    response_model=RecentErrorsResponse,
    summary="Most recent error events (level error/critical or HTTP 5xx)",
    responses=RESPONSES,
)
def project_recent_errors(
    scope: ProjectRead,
    service: MetricsServiceDep,
    start: From = None,
    end: To = None,
    limit: RecentLimit = RECENT_ERRORS_DEFAULT,
) -> RecentErrorsResponse:
    return service.recent_errors(scope, start, end, limit)


@router.get(
    "/organizations/{org_id}/metrics/summary",
    response_model=OrgSummaryResponse,
    summary="Organization dashboard: totals and per-project metrics over active projects",
    responses=RESPONSES,
)
def org_summary(
    scope: OrgRead,
    service: MetricsServiceDep,
    start: From = None,
    end: To = None,
    recent_limit: RecentLimit = RECENT_ERRORS_DEFAULT,
) -> OrgSummaryResponse:
    return service.org_summary(scope, start, end, recent_limit)


@router.get(
    "/organizations/{org_id}/metrics/timeseries",
    response_model=TimeseriesResponse,
    summary="Zero-filled time buckets across the organization's active projects",
    responses=RESPONSES,
)
def org_timeseries(
    scope: OrgRead, service: MetricsServiceDep, start: From = None, end: To = None
) -> TimeseriesResponse:
    return service.org_timeseries(scope, start, end)
