"""Log explorer routes (session auth; any project member, including viewers)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import AwareDatetime, StringConstraints

from app.api.deps import LogServiceDep, require_csrf, require_project_permission
from app.core.pagination import LOG_DEFAULT_LIMIT, LOG_MAX_LIMIT
from app.core.permissions import Permission
from app.schemas.common import problem_responses
from app.schemas.logs import Level, LogEventDetail, LogFacetsResponse, LogListResponse
from app.services.logs import LogQuery
from app.services.projects import ProjectScope

router = APIRouter(
    prefix="/projects/{project_id}/logs", tags=["logs"], dependencies=[Depends(require_csrf)]
)

ProjectRead = Annotated[ProjectScope, Depends(require_project_permission(Permission.PROJECT_READ))]
From = Annotated[
    AwareDatetime | None,
    Query(alias="from", description="Inclusive start (ISO 8601 with offset). Default: to - 24h"),
]
To = Annotated[AwareDatetime | None, Query(alias="to", description="Exclusive end. Default: now")]
ServiceName = Annotated[str, StringConstraints(min_length=1, max_length=100)]


@router.get(
    "",
    response_model=LogListResponse,
    summary="Search and list log events (newest first, keyset pagination)",
    responses=problem_responses(401, 404, 422),
)
def list_logs(
    scope: ProjectRead,
    service: LogServiceDep,
    start: From = None,
    end: To = None,
    level: Annotated[list[Level] | None, Query(max_length=5)] = None,
    service_name: Annotated[list[ServiceName] | None, Query(max_length=20)] = None,
    environment: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    trace_id: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    request_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Full-text search on message (prefix match per word)"),
    ] = None,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=LOG_MAX_LIMIT)] = LOG_DEFAULT_LIMIT,
) -> LogListResponse:
    return service.list(
        scope,
        LogQuery(
            start=start,
            end=end,
            levels=list(level or []),
            services=list(service_name or []),
            environment=environment,
            trace_id=trace_id,
            request_id=request_id,
            q=q,
            cursor=cursor,
            limit=limit,
        ),
    )


@router.get(
    "/facets",
    response_model=LogFacetsResponse,
    summary="Levels, services and environments present in the range (with counts)",
    responses=problem_responses(401, 404, 422),
)
def log_facets(
    scope: ProjectRead, service: LogServiceDep, start: From = None, end: To = None
) -> LogFacetsResponse:
    return service.facets(scope, start, end)


@router.get(
    "/{log_id}",
    response_model=LogEventDetail,
    summary="One log event with its attributes",
    responses=problem_responses(401, 404, 422),
)
def get_log(
    scope: ProjectRead,
    service: LogServiceDep,
    log_id: Annotated[int, Path(ge=1, le=2**63 - 1)],
) -> LogEventDetail:
    return service.get(scope, log_id)
