"""Read-side queries over log_events: explorer, facets and metrics (Phase 4).

All metric definitions live in the SQL predicates below so that the API, the docs
(docs/METRICS.md) and the tests share one source of truth. Every statement is parameterized.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    Interval,
    and_,
    bindparam,
    func,
    literal_column,
    or_,
    select,
    tuple_,
    type_coerce,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.core.time_range import BUCKET_ORIGIN, TimeRange
from app.models.log_event import LogEvent
from app.models.project import Project

# --- metric definitions (keep in sync with docs/METRICS.md) ---------------------------------
ERROR_LEVELS = ("error", "critical")
#: A request is an event that carries an HTTP status code.
IS_REQUEST = LogEvent.http_status.is_not(None)
#: An error is an event with level error/critical OR an HTTP status >= 500.
IS_ERROR = or_(LogEvent.level.in_(ERROR_LEVELS), LogEvent.http_status >= 500)
#: A latency sample is a request that also carries duration_ms.
IS_SAMPLE = and_(IS_REQUEST, LogEvent.duration_ms.is_not(None))


def aggregates(*, percentiles: tuple[float, ...] = (0.5, 0.95, 0.99)) -> list[Any]:
    """The aggregate columns every metric query shares (one scan, FILTER clauses)."""
    pcts = func.percentile_cont(postgresql.array(percentiles)).within_group(LogEvent.duration_ms)
    return [
        func.count().label("events"),
        func.count().filter(IS_REQUEST).label("requests"),
        func.count().filter(IS_ERROR).label("errors"),
        func.count().filter(and_(IS_REQUEST, IS_ERROR)).label("request_errors"),
        func.count().filter(IS_SAMPLE).label("samples"),
        type_coerce(pcts.filter(IS_SAMPLE), postgresql.ARRAY(Float)).label("pcts"),
    ]


def in_scope(project_ids: Sequence[uuid.UUID], rng: TimeRange) -> Any:
    return and_(
        LogEvent.project_id.in_(list(project_ids)), LogEvent.ts >= rng.start, LogEvent.ts < rng.end
    )


MAX_SEARCH_TOKENS = 8
MAX_TOKEN_LENGTH = 64


def _is_token_char(char: str) -> bool:
    # Word characters plus combining marks (Bengali vowel signs, virama, …), which PostgreSQL's
    # parser keeps inside a word but Python's `\w` does not. None of them is a tsquery operator.
    return char.isalnum() or char == "_" or unicodedata.category(char).startswith("M")


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    current: list[str] = []
    for char in text:
        if _is_token_char(char):
            current.append(char)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


SUMMARY_COLUMNS = (
    LogEvent.id,
    LogEvent.event_id,
    LogEvent.ts,
    LogEvent.level,
    LogEvent.service,
    LogEvent.environment,
    LogEvent.message,
    LogEvent.trace_id,
    LogEvent.request_id,
    LogEvent.http_method,
    LogEvent.http_path,
    LogEvent.http_status,
    LogEvent.duration_ms,
    LogEvent.is_synthetic,
)


def search_tsquery(q: str | None) -> str | None:
    """Turn free text into a safe prefix tsquery: `timeout db` -> `timeout:* & db:*`.

    Only word tokens (letters, digits, `_`, combining marks) survive, so tsquery operators
    (& | ! ( ) : * ') supplied by the user can never reach the parser; the result is still
    passed as a bound parameter.
    """
    if not q:
        return None
    tokens = [t[:MAX_TOKEN_LENGTH] for t in _tokens(q.lower())][:MAX_SEARCH_TOKENS]
    if not tokens:
        return None
    return " & ".join(f"{token}:*" for token in tokens)


@dataclass(frozen=True, slots=True)
class LogFilters:
    levels: Sequence[str] = ()
    services: Sequence[str] = ()
    environment: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    tsquery: str | None = None


class LogQueryRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --- explorer -----------------------------------------------------------------------------
    def _filtered(self, stmt: Any, project_id: uuid.UUID, rng: TimeRange, f: LogFilters) -> Any:
        stmt = stmt.where(
            LogEvent.project_id == project_id, LogEvent.ts >= rng.start, LogEvent.ts < rng.end
        )
        if f.levels:
            stmt = stmt.where(LogEvent.level.in_(list(f.levels)))
        if f.services:
            stmt = stmt.where(LogEvent.service.in_(list(f.services)))
        if f.environment:
            stmt = stmt.where(LogEvent.environment == f.environment)
        if f.trace_id:
            stmt = stmt.where(LogEvent.trace_id == f.trace_id)
        if f.request_id:
            stmt = stmt.where(LogEvent.request_id == f.request_id)
        if f.tsquery:
            stmt = stmt.where(
                LogEvent.message_tsv.op("@@")(
                    func.to_tsquery(literal_column("'simple'::regconfig"), f.tsquery)
                )
            )
        return stmt

    def list_logs(
        self,
        project_id: uuid.UUID,
        rng: TimeRange,
        filters: LogFilters,
        *,
        before: tuple[datetime, int] | None,
        limit: int,
    ) -> list[Any]:
        stmt = self._filtered(select(*SUMMARY_COLUMNS), project_id, rng, filters)
        if before is not None:
            stmt = stmt.where(tuple_(LogEvent.ts, LogEvent.id) < tuple_(before[0], before[1]))
        stmt = stmt.order_by(LogEvent.ts.desc(), LogEvent.id.desc()).limit(limit + 1)
        return list(self.db.execute(stmt).all())

    def get_log(self, project_id: uuid.UUID, log_id: int) -> LogEvent | None:
        stmt = select(LogEvent).where(LogEvent.id == log_id, LogEvent.project_id == project_id)
        return self.db.scalar(stmt)

    def facet(
        self, column: Any, project_id: uuid.UUID, rng: TimeRange, limit: int
    ) -> list[tuple[str, int]]:
        count = func.count().label("n")
        stmt = (
            select(column, count)
            .where(
                LogEvent.project_id == project_id, LogEvent.ts >= rng.start, LogEvent.ts < rng.end
            )
            .group_by(column)
            .order_by(count.desc(), column)
            .limit(limit)
        )
        return [(str(value), int(n)) for value, n in self.db.execute(stmt).all()]

    def recent_errors(
        self, project_ids: Sequence[uuid.UUID], rng: TimeRange, limit: int
    ) -> list[Any]:
        stmt = (
            select(*SUMMARY_COLUMNS, LogEvent.project_id, Project.name.label("project_name"))
            .join(Project, Project.id == LogEvent.project_id)
            .where(
                LogEvent.project_id.in_(list(project_ids)),
                LogEvent.ts >= rng.start,
                LogEvent.ts < rng.end,
                IS_ERROR,
            )
            .order_by(LogEvent.ts.desc(), LogEvent.id.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).all())

    # --- metrics ------------------------------------------------------------------------------
    def totals(self, project_ids: Sequence[uuid.UUID], rng: TimeRange) -> dict[str, Any]:
        stmt = select(*aggregates()).where(in_scope(project_ids, rng))
        return dict(self.db.execute(stmt).mappings().one())

    def per_project(self, project_ids: Sequence[uuid.UUID], rng: TimeRange) -> list[dict[str, Any]]:
        stmt = (
            select(LogEvent.project_id, *aggregates())
            .where(in_scope(project_ids, rng))
            .group_by(LogEvent.project_id)
        )
        return [dict(r) for r in self.db.execute(stmt).mappings()]

    def per_service(
        self, project_id: uuid.UUID, rng: TimeRange, limit: int
    ) -> list[dict[str, Any]]:
        events = func.count()
        stmt = (
            select(LogEvent.service, *aggregates())
            .where(in_scope([project_id], rng))
            .group_by(LogEvent.service)
            .order_by(events.desc(), LogEvent.service)
            .limit(limit)
        )
        return [dict(r) for r in self.db.execute(stmt).mappings()]

    def timeseries(
        self, project_ids: Sequence[uuid.UUID], rng: TimeRange, step: timedelta
    ) -> list[dict[str, Any]]:
        """Zero-filled buckets: generate_series of date_bin starts LEFT JOIN the aggregates."""
        step_p = bindparam("step", step, type_=Interval())
        origin = bindparam("origin", BUCKET_ORIGIN, type_=DateTime(timezone=True))
        first = func.date_bin(
            step_p, bindparam("first", rng.start, type_=DateTime(timezone=True)), origin
        )
        last = bindparam("last", rng.end - timedelta(microseconds=1), type_=DateTime(timezone=True))
        series = select(func.generate_series(first, last, step_p).label("bucket")).subquery()
        bucket = func.date_bin(step_p, LogEvent.ts, origin)
        agg = (
            select(bucket.label("bucket"), *aggregates(percentiles=(0.95,)))
            .where(in_scope(project_ids, rng))
            .group_by(literal_column("1"))
            .subquery()
        )
        stmt = (
            select(
                series.c.bucket,
                func.coalesce(agg.c.events, 0).label("events"),
                func.coalesce(agg.c.requests, 0).label("requests"),
                func.coalesce(agg.c.errors, 0).label("errors"),
                func.coalesce(agg.c.request_errors, 0).label("request_errors"),
                func.coalesce(agg.c.samples, 0).label("samples"),
                agg.c.pcts,
            )
            .select_from(series.outerjoin(agg, agg.c.bucket == series.c.bucket))
            .order_by(series.c.bucket)
        )
        return [dict(r) for r in self.db.execute(stmt).mappings()]
