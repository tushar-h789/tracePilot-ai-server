"""Metrics endpoints against hand-computed fixtures (definitions: docs/METRICS.md)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from tests.helpers import ApiUser, insert_events, iso

pytestmark = pytest.mark.integration

# Bucket-aligned anchor two hours ago; the window [START, END) is exactly 3 h -> 1-minute buckets.
START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
END = START + timedelta(hours=3)
WINDOW = {"from": iso(START), "to": iso(END)}


def at(minutes: float) -> datetime:
    return START + timedelta(minutes=minutes)


def fixture_rows() -> list[dict[str, Any]]:
    """17 events in the window (+2 outside). Expected values are computed by hand in the tests.

    - 10 x api 200, durations 10..100, at minute 10 (one bucket with 10 samples)
    - 2 x api 500 level=error, durations 200/300, at minute 20 (2 samples)
    - 1 x worker critical, no HTTP (error, not a request), minute 30
    - 3 x worker info, no HTTP, minute 40
    - 1 x web 404 warn without duration (request, not a sample), minute 50
    - outside: one at START - 1 s and one exactly at END (half-open range excludes both)
    """
    rows: list[dict[str, Any]] = []
    for n in range(10):
        rows.append(
            {"ts": at(10), "service": "api", "http_status": 200, "duration_ms": 10.0 * (n + 1)}
        )
    for d in (200.0, 300.0):
        rows.append(
            {"ts": at(20), "service": "api", "level": "error", "http_status": 500, "duration_ms": d}
        )
    rows.append({"ts": at(30), "service": "worker", "level": "critical", "message": "boom"})
    rows += [{"ts": at(40), "service": "worker"} for _ in range(3)]
    rows.append({"ts": at(50), "service": "web", "level": "warning", "http_status": 404})
    rows.append({"ts": START - timedelta(seconds=1), "level": "error", "http_status": 500})
    rows.append({"ts": END, "level": "error", "http_status": 500})
    return rows


@dataclass
class Org:
    owner: ApiUser
    org_id: str
    project_id: str


@pytest.fixture
def org(app: FastAPI) -> Org:
    owner = ApiUser(app).signup()
    o = owner.create_org("Metrics Org")
    p = owner.create_project(o["id"], "Metrics Project")
    return Org(owner, o["id"], p["id"])


def test_overview_matches_hand_computed_values(org: Org, db_session: Session) -> None:
    insert_events(db_session, org.project_id, fixture_rows())
    r = org.owner.get(f"/projects/{org.project_id}/metrics/overview", WINDOW)
    assert r.status_code == 200, r.text
    t = r.json()["totals"]
    assert t["events"] == 17
    assert t["requests"] == 13  # 10 + 2 + 1 (404)
    assert t["errors"] == 3  # 2 x 500 + critical
    assert t["request_errors"] == 2
    assert t["error_rate"] == pytest.approx(2 / 13)
    assert t["error_rate_reason"] is None
    # 12 samples < 20 minimum -> percentiles withheld, with a reason.
    assert t["latency"] == {
        "p50_ms": None,
        "p95_ms": None,
        "p99_ms": None,
        "samples": 12,
        "min_samples": 20,
        "reason": "insufficient_samples",
    }


def test_no_data_gives_nulls_with_reasons(org: Org) -> None:
    t = org.owner.get(f"/projects/{org.project_id}/metrics/overview", WINDOW).json()["totals"]
    assert (t["events"], t["requests"], t["errors"]) == (0, 0, 0)
    assert t["error_rate"] is None
    assert t["error_rate_reason"] == "no_requests"
    assert t["latency"]["reason"] == "no_latency_samples"
    assert t["latency"]["p95_ms"] is None


def test_events_without_requests_have_null_error_rate(org: Org, db_session: Session) -> None:
    insert_events(db_session, org.project_id, [{"ts": at(1), "level": "error"}])
    t = org.owner.get(f"/projects/{org.project_id}/metrics/overview", WINDOW).json()["totals"]
    assert (t["events"], t["errors"], t["requests"]) == (1, 1, 0)
    assert t["error_rate"] is None


def test_percentiles_use_percentile_cont(org: Org, db_session: Session) -> None:
    rows = [
        {"ts": at(5), "http_status": 200, "duration_ms": float(d)} for d in range(1, 26)
    ]  # 25 samples: 1..25
    insert_events(db_session, org.project_id, rows)
    lat = org.owner.get(f"/projects/{org.project_id}/metrics/overview", WINDOW).json()["totals"][
        "latency"
    ]
    # percentile_cont(p) = 1 + p * (25 - 1) for evenly spaced 1..25
    assert lat["p50_ms"] == pytest.approx(13.0)
    assert lat["p95_ms"] == pytest.approx(23.8)
    assert lat["p99_ms"] == pytest.approx(24.76)
    assert (lat["samples"], lat["reason"]) == (25, None)


def test_timeseries_zero_filled_buckets(org: Org, db_session: Session) -> None:
    insert_events(db_session, org.project_id, fixture_rows())
    r = org.owner.get(f"/projects/{org.project_id}/metrics/timeseries", WINDOW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket_seconds"] == 60
    assert body["min_samples"] == 5
    points = body["points"]
    assert len(points) == 180
    starts = [datetime.fromisoformat(p["bucket_start"]) for p in points]
    assert starts[0] == START
    assert all(b - a == timedelta(minutes=1) for a, b in itertools.pairwise(starts))
    by_minute = {
        round((s - START).total_seconds() / 60): p for s, p in zip(starts, points, strict=True)
    }
    assert sum(p["events"] for p in points) == 17
    assert by_minute[10]["requests"] == 10
    assert by_minute[10]["samples"] == 10
    # 10 samples 10..100: p95 = 10 + 0.95 * 90 = 95.5
    assert by_minute[10]["p95_ms"] == pytest.approx(95.5)
    # 2 samples < 5 per bucket -> null
    assert by_minute[20]["samples"] == 2
    assert by_minute[20]["p95_ms"] is None
    assert (by_minute[20]["errors"], by_minute[20]["request_errors"]) == (2, 2)
    assert (by_minute[30]["errors"], by_minute[30]["requests"]) == (1, 0)
    empty = by_minute[0]
    assert (empty["events"], empty["requests"], empty["errors"], empty["p95_ms"]) == (0, 0, 0, None)


@pytest.mark.parametrize(
    ("span", "bucket_seconds", "max_points"),
    [
        (timedelta(hours=24), 900, 97),
        (timedelta(days=7), 3600, 169),
        (timedelta(days=30), 21600, 121),
    ],
)
def test_timeseries_bucket_sizes(
    org: Org, span: timedelta, bucket_seconds: int, max_points: int
) -> None:
    end = datetime.now(UTC)
    body = org.owner.get(
        f"/projects/{org.project_id}/metrics/timeseries", {"from": iso(end - span), "to": iso(end)}
    ).json()
    assert body["bucket_seconds"] == bucket_seconds
    assert max_points - 1 <= len(body["points"]) <= max_points


def test_services_summary(org: Org, db_session: Session) -> None:
    insert_events(db_session, org.project_id, fixture_rows())
    body = org.owner.get(f"/projects/{org.project_id}/metrics/services", WINDOW).json()
    items = {s["service_name"]: s for s in body["items"]}
    assert [s["service_name"] for s in body["items"]] == ["api", "worker", "web"]
    assert (items["api"]["events"], items["api"]["requests"], items["api"]["errors"]) == (12, 12, 2)
    assert items["api"]["error_rate"] == pytest.approx(2 / 12)
    assert items["api"]["p95_ms"] is None  # 12 samples < 20
    assert items["worker"]["error_rate"] is None  # no requests
    assert items["worker"]["errors"] == 1
    assert (items["web"]["requests"], items["web"]["error_rate"]) == (1, 0.0)


def test_recent_errors(org: Org, db_session: Session) -> None:
    insert_events(db_session, org.project_id, fixture_rows())
    body = org.owner.get(f"/projects/{org.project_id}/metrics/recent-errors", WINDOW).json()
    assert [i["level"] for i in body["items"]] == ["critical", "error", "error"]
    limited = org.owner.get(
        f"/projects/{org.project_id}/metrics/recent-errors", {**WINDOW, "limit": 1}
    ).json()
    assert len(limited["items"]) == 1
    for limit in (0, 51):
        r = org.owner.get(f"/projects/{org.project_id}/metrics/recent-errors", {"limit": limit})
        assert r.status_code == 422


def test_org_summary_aggregates_active_projects(org: Org, db_session: Session) -> None:
    second = org.owner.create_project(org.org_id, "Second")
    archived = org.owner.create_project(org.org_id, "Archived")
    insert_events(db_session, org.project_id, fixture_rows())
    insert_events(
        db_session, second["id"], [{"ts": at(70), "http_status": 503, "duration_ms": 5.0}]
    )
    insert_events(db_session, archived["id"], [{"ts": at(80), "level": "error"}])
    assert org.owner.post(f"/projects/{archived['id']}/archive").status_code == 200
    r = org.owner.get(f"/organizations/{org.org_id}/metrics/summary", WINDOW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["project_count"] == 2
    t = body["totals"]
    assert (t["events"], t["requests"], t["errors"], t["request_errors"]) == (18, 14, 4, 3)
    assert t["error_rate"] == pytest.approx(3 / 14)
    names = [p["name"] for p in body["projects"]]
    assert names == ["Metrics Project", "Second"]  # events desc
    assert body["projects"][1]["error_rate"] == 1.0
    assert next(e["project_name"] for e in body["recent_errors"]) == "Second"
    assert len(body["recent_errors"]) == 4
    series = org.owner.get(f"/organizations/{org.org_id}/metrics/timeseries", WINDOW).json()
    assert sum(p["events"] for p in series["points"]) == 18


def test_org_without_projects_is_empty_not_error(app: FastAPI) -> None:
    owner = ApiUser(app).signup()
    o = owner.create_org("Empty Org")
    body = owner.get(f"/organizations/{o['id']}/metrics/summary").json()
    assert body["project_count"] == 0
    assert body["projects"] == body["recent_errors"] == []
    assert body["totals"]["error_rate"] is None
    series = owner.get(f"/organizations/{o['id']}/metrics/timeseries").json()
    assert series["points"]
    assert all(p["events"] == 0 for p in series["points"])
