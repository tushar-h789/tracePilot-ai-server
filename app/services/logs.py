"""Log explorer: filtered, keyset-paginated reads within one authorized project."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.pagination import decode_log_cursor, encode_log_cursor
from app.core.time_range import TimeRange, resolve_range
from app.models.log_event import LogEvent
from app.repositories.log_queries import LogFilters, LogQueryRepository, search_tsquery
from app.schemas.logs import (
    FacetValue,
    LogEventDetail,
    LogEventSummary,
    LogFacetsResponse,
    LogListResponse,
)
from app.services.projects import ProjectScope

FACET_SERVICES_LIMIT = 100
FACET_ENVIRONMENTS_LIMIT = 20
LOG_NOT_FOUND = "Log event not found."


def summary_from_row(row: Any) -> LogEventSummary:
    return LogEventSummary(
        id=row.id,
        event_id=row.event_id,
        timestamp=row.ts,
        level=row.level,
        service_name=row.service,
        environment=row.environment,
        message=row.message,
        trace_id=row.trace_id,
        request_id=row.request_id,
        http_method=row.http_method,
        http_path=row.http_path,
        http_status=row.http_status,
        duration_ms=row.duration_ms,
        is_synthetic=row.is_synthetic,
    )


def detail_from_model(event: LogEvent) -> LogEventDetail:
    return LogEventDetail(
        **summary_from_row(event).model_dump(),
        received_at=event.received_at,
        attributes=event.attributes,
    )


@dataclass(frozen=True, slots=True)
class LogQuery:
    start: datetime | None
    end: datetime | None
    levels: list[str]
    services: list[str]
    environment: str | None
    trace_id: str | None
    request_id: str | None
    q: str | None
    cursor: str | None
    limit: int


class LogService:
    def __init__(self, db: Session) -> None:
        self.repo = LogQueryRepository(db)

    def list(self, scope: ProjectScope, query: LogQuery) -> LogListResponse:
        rng = resolve_range(query.start, query.end)
        before = decode_log_cursor(query.cursor)
        filters = LogFilters(
            levels=sorted(set(query.levels)),
            services=sorted(set(query.services)),
            environment=query.environment,
            trace_id=query.trace_id,
            request_id=query.request_id,
            tsquery=search_tsquery(query.q),
        )
        rows = self.repo.list_logs(scope.project_id, rng, filters, before=before, limit=query.limit)
        page = rows[: query.limit]
        next_cursor = (
            encode_log_cursor(page[-1].ts, page[-1].id) if len(rows) > query.limit else None
        )
        return LogListResponse(
            items=[summary_from_row(r) for r in page],
            next_cursor=next_cursor,
            range_from=rng.start,
            range_to=rng.end,
        )

    def get(self, scope: ProjectScope, log_id: int) -> LogEventDetail:
        event = self.repo.get_log(scope.project_id, log_id)
        if event is None:
            raise NotFoundError(LOG_NOT_FOUND)
        return detail_from_model(event)

    def facets(
        self, scope: ProjectScope, start: datetime | None, end: datetime | None
    ) -> LogFacetsResponse:
        rng: TimeRange = resolve_range(start, end)

        def values(column: Any, limit: int) -> list[FacetValue]:
            return [
                FacetValue(value=v, count=n)
                for v, n in self.repo.facet(column, scope.project_id, rng, limit)
            ]

        return LogFacetsResponse(
            range_from=rng.start,
            range_to=rng.end,
            levels=values(LogEvent.level, 5),
            services=values(LogEvent.service, FACET_SERVICES_LIMIT),
            environments=values(LogEvent.environment, FACET_ENVIRONMENTS_LIMIT),
        )
