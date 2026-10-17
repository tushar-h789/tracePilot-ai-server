"""Time-range resolution and bucket selection shared by the logs and metrics APIs.

Rules (documented in docs/METRICS.md):
- Ranges are half-open `[from, to)` in UTC; both bounds must be timezone-aware.
- Defaults: `to = now`, `from = to - 24 h`. Supplying only `from` sets `to = now`; supplying only
  `to` sets `from = to - 24 h`.
- `from` must be before `to` and the span may not exceed 30 days (422 otherwise).
- Time-series buckets use the smallest step from BUCKET_STEPS that yields at most MAX_BUCKETS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.errors import ValidationFailedError

DEFAULT_SPAN = timedelta(hours=24)
MAX_SPAN = timedelta(days=30)
MAX_BUCKETS = 200
BUCKET_STEPS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(days=1),
)
# date_bin origin: buckets align to wall-clock boundaries in UTC.
BUCKET_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    @property
    def span(self) -> timedelta:
        return self.end - self.start


def _invalid(field: str, msg: str) -> ValidationFailedError:
    return ValidationFailedError(
        msg, errors=[{"loc": ["query", field], "msg": msg, "type": "range"}]
    )


def resolve_range(
    start: datetime | None, end: datetime | None, *, now: datetime | None = None
) -> TimeRange:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    if end is None:
        end = now
    if start is None:
        start = end - DEFAULT_SPAN
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if start >= end:
        raise _invalid("from", "`from` must be earlier than `to`.")
    if end - start > MAX_SPAN:
        raise _invalid("from", "The time range may span at most 30 days.")
    return TimeRange(start=start, end=end)


def bucket_step(span: timedelta) -> timedelta:
    for step in BUCKET_STEPS:
        if span / step <= MAX_BUCKETS:
            return step
    return BUCKET_STEPS[-1]  # pragma: no cover - MAX_SPAN keeps us within the table
