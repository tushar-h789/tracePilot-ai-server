"""Unit tests for Phase 4 building blocks: ranges, buckets, log cursors and search tokens."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.errors import ValidationFailedError
from app.core.pagination import decode_log_cursor, encode_log_cursor
from app.core.time_range import MAX_BUCKETS, bucket_step, resolve_range
from app.repositories.log_queries import search_tsquery
from app.services.metrics import error_rate, latency

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def test_default_range_is_last_24h() -> None:
    rng = resolve_range(None, None, now=NOW)
    assert (rng.start, rng.end) == (NOW - timedelta(hours=24), NOW)
    only_to = resolve_range(None, NOW - timedelta(hours=1), now=NOW)
    assert only_to.span == timedelta(hours=24)
    only_from = resolve_range(NOW - timedelta(hours=2), None, now=NOW)
    assert only_from.end == NOW


def test_range_is_normalized_to_utc() -> None:
    plus6 = timezone(timedelta(hours=6))
    rng = resolve_range(datetime(2026, 10, 17, 10, tzinfo=plus6), NOW, now=NOW)
    assert rng.start == datetime(2026, 10, 17, 4, tzinfo=UTC)
    assert rng.start.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (NOW, NOW),
        (NOW, NOW - timedelta(seconds=1)),
        (NOW - timedelta(days=30, seconds=1), NOW),
    ],
)
def test_invalid_ranges(start: datetime, end: datetime) -> None:
    with pytest.raises(ValidationFailedError):
        resolve_range(start, end, now=NOW)


def test_thirty_days_exactly_is_allowed() -> None:
    assert resolve_range(NOW - timedelta(days=30), NOW, now=NOW).span == timedelta(days=30)


@pytest.mark.parametrize(
    ("span", "step"),
    [
        (timedelta(minutes=10), timedelta(minutes=1)),
        (timedelta(hours=1), timedelta(minutes=1)),
        (timedelta(minutes=200), timedelta(minutes=1)),
        (timedelta(minutes=201), timedelta(minutes=5)),
        (timedelta(hours=24), timedelta(minutes=15)),
        (timedelta(days=7), timedelta(hours=1)),
        (timedelta(days=30), timedelta(hours=6)),
    ],
)
def test_bucket_step(span: timedelta, step: timedelta) -> None:
    assert bucket_step(span) == step
    assert span / step <= MAX_BUCKETS


def test_log_cursor_round_trip() -> None:
    ts = datetime(2026, 10, 17, 1, 2, 3, 456789, tzinfo=UTC)
    assert decode_log_cursor(encode_log_cursor(ts, 42)) == (ts, 42)
    assert decode_log_cursor(None) is None


def _b64(payload: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        "!!!",
        _b64([1, 2]),
        _b64({"v": 2, "ts": "2026-10-17T00:00:00+00:00", "id": 1}),
        _b64({"v": 1, "ts": "2026-10-17T00:00:00", "id": 1}),  # naive
        _b64({"v": 1, "ts": "yesterday", "id": 1}),
        _b64({"v": 1, "ts": "2026-10-17T00:00:00+00:00", "id": "1"}),
        _b64({"v": 1, "ts": "2026-10-17T00:00:00+00:00", "id": True}),
        _b64({"v": 1, "ts": "2026-10-17T00:00:00+00:00", "id": 0}),
        _b64({"v": 1, "ts": "2026-10-17T00:00:00+00:00"}),
    ],
)
def test_bad_log_cursor_is_validation_error(cursor: str) -> None:
    with pytest.raises(ValidationFailedError):
        decode_log_cursor(cursor)


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        ("Timeout", "timeout:*"),
        ("db timeout", "db:* & timeout:*"),
        ("a & b | !c", "a:* & b:* & c:*"),
        ("it's (fine):*", "it:* & s:* & fine:*"),
        ("'; DROP TABLE log_events; --", "drop:* & table:* & log_events:*"),
        ("&|!():*'\\", None),
        ("ঢাকা সার্ভার", "ঢাকা:* & সার্ভার:*"),
        (" ".join(f"w{i}" for i in range(12)), " & ".join(f"w{i}:*" for i in range(8))),
        ("x" * 100, "x" * 64 + ":*"),
    ],
)
def test_search_tsquery(q: str | None, expected: str | None) -> None:
    assert search_tsquery(q) == expected


def test_error_rate_and_latency_nulls() -> None:
    assert error_rate(0, 0) == (None, "no_requests")
    assert error_rate(4, 1) == (0.25, None)
    empty = latency({"samples": 0, "pcts": None})
    assert (empty.p95_ms, empty.reason) == (None, "no_latency_samples")
    few = latency({"samples": 19, "pcts": [1.0, 2.0, 3.0]})
    assert (few.p50_ms, few.p95_ms, few.reason) == (None, None, "insufficient_samples")
    enough = latency({"samples": 20, "pcts": [1.0, 2.0, 3.0]})
    assert (enough.p50_ms, enough.p99_ms, enough.reason) == (1.0, 3.0, None)
