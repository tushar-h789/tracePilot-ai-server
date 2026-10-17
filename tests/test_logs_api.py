"""GET /projects/{id}/logs: filters, search, keyset pagination, ranges, detail and facets."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session

from tests.helpers import ApiUser, insert_events, iso

pytestmark = pytest.mark.integration

# A fixed window an hour in the past keeps every row inside the default 24 h range.
T0 = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=1)
WINDOW = {"from": iso(T0 - timedelta(hours=2)), "to": iso(T0 + timedelta(minutes=1))}
LEVELS = ("debug", "info", "warning", "error", "critical")
SERVICES = ("api", "worker", "web")
MESSAGES = (
    "Timeout connecting to db-primary",
    "GET /api/cart completed",
    "Payment declined by provider",
    "Cache miss for user profile",
)


def dataset() -> list[dict[str, Any]]:
    rows = []
    for i in range(40):
        rows.append(
            {
                # Pairs of rows share a timestamp to exercise the (ts, id) tie-breaker.
                "ts": T0 - timedelta(minutes=i // 2),
                "level": LEVELS[i % 5],
                "service": SERVICES[i % 3],
                "environment": "staging" if i % 4 == 0 else "production",
                "message": f"{MESSAGES[i % 4]} #{i}",
                "trace_id": f"trace{i % 6:02d}",
                "request_id": f"req-{i}",
                "http_status": 500 if i % 7 == 0 else 200,
                "duration_ms": float(i),
                "attributes": {"i": i, "nested": {"ok": True}},
            }
        )
    return rows


@dataclass
class Seeded:
    owner: ApiUser
    org_id: str
    project_id: str
    rows: list[dict[str, Any]]
    ids: list[int]

    def url(self, suffix: str = "") -> str:
        return f"/projects/{self.project_id}/logs{suffix}"

    def expected(self, keep: Callable[[dict[str, Any]], bool]) -> list[int]:
        """Ids of matching rows in API order: ts DESC, id DESC."""
        pairs = [(r["ts"], i) for r, i in zip(self.rows, self.ids, strict=True) if keep(r)]
        return [i for _, i in sorted(pairs, reverse=True)]


@pytest.fixture
def seeded(app: FastAPI, db_session: Session) -> Seeded:
    owner = ApiUser(app).signup()
    org = owner.create_org("Logs Org")
    project = owner.create_project(org["id"], "Logs Project")
    rows = dataset()
    ids = insert_events(db_session, project["id"], rows)
    return Seeded(owner, org["id"], project["id"], rows, ids)


def _ids(response: Any) -> list[int]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_default_list_returns_last_24h_newest_first(seeded: Seeded, db_session: Session) -> None:
    old = insert_events(
        db_session, seeded.project_id, [{"ts": datetime.now(UTC) - timedelta(hours=25)}]
    )
    r = seeded.owner.get(seeded.url(), {"limit": 200})
    ids = _ids(r)
    assert ids == seeded.expected(lambda _: True)
    assert old[0] not in ids
    body = r.json()
    assert body["next_cursor"] is None
    assert set(body) == {"items", "next_cursor", "range_from", "range_to"}
    first = body["items"][0]
    assert "attributes" not in first  # list stays light; detail carries attributes
    assert first["service_name"] in SERVICES


def test_default_limit_is_50(seeded: Seeded, db_session: Session) -> None:
    insert_events(
        db_session, seeded.project_id, [{"ts": T0 - timedelta(seconds=s)} for s in range(20)]
    )
    r = seeded.owner.get(seeded.url())
    assert len(r.json()["items"]) == 50
    assert r.json()["next_cursor"]


FILTERS: list[tuple[dict[str, Any], Callable[[dict[str, Any]], bool]]] = [
    ({"level": ["error"]}, lambda r: r["level"] == "error"),
    ({"level": ["error", "critical"]}, lambda r: r["level"] in ("error", "critical")),
    ({"service_name": ["worker"]}, lambda r: r["service"] == "worker"),
    ({"service_name": ["api", "web"]}, lambda r: r["service"] in ("api", "web")),
    ({"environment": "staging"}, lambda r: r["environment"] == "staging"),
    ({"trace_id": "trace03"}, lambda r: r["trace_id"] == "trace03"),
    ({"request_id": "req-7"}, lambda r: r["request_id"] == "req-7"),
    ({"q": "timeout"}, lambda r: "Timeout" in r["message"]),
    ({"q": "TIME"}, lambda r: "Timeout" in r["message"]),  # prefix, case-insensitive
    ({"q": "payment declined"}, lambda r: "Payment declined" in r["message"]),
    ({"q": "declined payment"}, lambda r: "Payment declined" in r["message"]),  # AND, any order
    ({"q": "timeout cart"}, lambda r: False),
]
COMBOS = [
    (
        {**fa, **fb},
        (lambda a, b: lambda r: a(r) and b(r))(pa, pb),
    )
    for (fa, pa), (fb, pb) in itertools.combinations(FILTERS[:8], 2)
    if not set(fa) & set(fb)
]
COMBOS.append(
    (
        {
            "level": ["error", "info"],
            "service_name": ["api"],
            "environment": "production",
            "q": "db",
        },
        lambda r: (
            r["level"] in ("error", "info")
            and r["service"] == "api"
            and r["environment"] == "production"
            and "db-primary" in r["message"]
        ),
    )
)


@pytest.mark.parametrize(("params", "keep"), FILTERS + COMBOS)
def test_filters_and_combinations(
    seeded: Seeded, params: dict[str, Any], keep: Callable[[dict[str, Any]], bool]
) -> None:
    got = _ids(seeded.owner.get(seeded.url(), {**WINDOW, **params, "limit": 200}))
    assert got == seeded.expected(keep)


def test_filters_match_something(seeded: Seeded) -> None:
    """Guard against vacuous filter tests: each single filter selects a non-empty strict subset."""
    for params, keep in FILTERS[:-1]:
        expected = seeded.expected(keep)
        assert 0 < len(expected) < len(seeded.rows), params


def test_unknown_level_and_oversized_values_are_422(seeded: Seeded) -> None:
    for params in (
        {"level": "fatal"},
        {"q": "x" * 201},
        {"trace_id": "t" * 65},
        {"service_name": "s" * 101},
        {"service_name": [f"s{i}" for i in range(21)]},
        {"environment": ""},
    ):
        r = seeded.owner.get(seeded.url(), params)
        assert r.status_code == 422, (params, r.text)
        assert r.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(
    "q",
    ["'", "\\", "&", "a & | b", "!(", ":*", "100%", "_", "'; DROP TABLE log_events; --", "ü"],
)
def test_search_escaping_never_errors(seeded: Seeded, q: str) -> None:
    r = seeded.owner.get(seeded.url(), {**WINDOW, "q": q, "limit": 200})
    assert r.status_code == 200, r.text


def test_punctuation_only_search_is_no_filter(seeded: Seeded) -> None:
    got = _ids(seeded.owner.get(seeded.url(), {**WINDOW, "q": "&|!()", "limit": 200}))
    assert got == seeded.expected(lambda _: True)


def test_keyset_pagination_is_stable_without_dupes_or_gaps(
    seeded: Seeded, db_session: Session
) -> None:
    pages: list[list[int]] = []
    cursor = None
    while True:
        params: dict[str, Any] = {**WINDOW, "limit": 7}
        if cursor:
            params["cursor"] = cursor
        r = seeded.owner.get(seeded.url(), params)
        pages.append(_ids(r))
        cursor = r.json()["next_cursor"]
        if len(pages) == 1:
            # Rows arriving after the first page (newer timestamps) must not shift later pages.
            insert_events(db_session, seeded.project_id, [{"ts": T0 + timedelta(seconds=30)}])
        if not cursor:
            break
    flat = [i for page in pages for i in page]
    assert flat == seeded.expected(lambda _: True)
    assert len(flat) == len(set(flat)) == 40
    assert [len(p) for p in pages] == [7, 7, 7, 7, 7, 5]


def test_pagination_with_filters(seeded: Seeded) -> None:
    params: dict[str, Any] = {**WINDOW, "service_name": "api", "limit": 3}
    seen: list[int] = []
    while True:
        body = seeded.owner.get(seeded.url(), params).json()
        seen += [i["id"] for i in body["items"]]
        if not body["next_cursor"]:
            break
        params["cursor"] = body["next_cursor"]
    assert seen == seeded.expected(lambda r: r["service"] == "api")


def test_exact_page_boundary_has_no_trailing_cursor(seeded: Seeded) -> None:
    body = seeded.owner.get(seeded.url(), {**WINDOW, "limit": 40}).json()
    assert len(body["items"]) == 40
    assert body["next_cursor"] is None


@pytest.mark.parametrize(("limit", "status"), [(0, 422), (1, 200), (200, 200), (201, 422)])
def test_limit_bounds(seeded: Seeded, limit: int, status: int) -> None:
    assert seeded.owner.get(seeded.url(), {"limit": limit}).status_code == status


@pytest.mark.parametrize("cursor", ["garbage", "eyJ2IjoxfQ", "x" * 301])
def test_bad_cursor_is_422(seeded: Seeded, cursor: str) -> None:
    r = seeded.owner.get(seeded.url(), {"cursor": cursor})
    assert r.status_code == 422, r.text


def test_time_range_validation_and_half_open_bounds(seeded: Seeded) -> None:
    owner, url = seeded.owner, seeded.url()
    now = datetime.now(UTC)
    bad = [
        {"from": iso(now), "to": iso(now)},
        {"from": iso(now), "to": iso(now - timedelta(minutes=1))},
        {"from": iso(now - timedelta(days=31)), "to": iso(now)},
        {"from": "2026-10-17T00:00:00"},  # naive
        {"to": "yesterday"},
    ]
    for params in bad:
        r = owner.get(url, params)
        assert r.status_code == 422, (params, r.text)
    # [from, to): a row exactly at `from` is included, one exactly at `to` is excluded.
    at_t0 = seeded.expected(lambda r: r["ts"] == T0)
    assert len(at_t0) == 2
    incl = _ids(owner.get(url, {"from": iso(T0), "to": iso(T0 + timedelta(seconds=1))}))
    assert incl == at_t0
    excl = _ids(owner.get(url, {"from": iso(T0 - timedelta(seconds=1)), "to": iso(T0)}))
    assert excl == []


def test_range_with_offset_is_converted_to_utc(seeded: Seeded) -> None:
    plus6 = T0.astimezone(timezone(timedelta(hours=6)))
    body = seeded.owner.get(
        seeded.url(), {"from": iso(plus6), "to": iso(plus6 + timedelta(seconds=1))}
    ).json()
    assert body["range_from"].endswith("Z") or body["range_from"].endswith("+00:00")
    assert len(body["items"]) == 2


def test_detail_includes_attributes_and_is_project_scoped(app: FastAPI, seeded: Seeded) -> None:
    log_id = seeded.ids[3]
    r = seeded.owner.get(seeded.url(f"/{log_id}"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attributes"] == {"i": 3, "nested": {"ok": True}}
    assert body["request_id"] == "req-3"
    assert "received_at" in body
    # Same user, another project of theirs: the id does not resolve there.
    other = seeded.owner.create_project(seeded.org_id, "Other Project")
    assert seeded.owner.get(f"/projects/{other['id']}/logs/{log_id}").status_code == 404
    missing = seeded.owner.get(seeded.url("/999999999"))
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Log event not found."
    assert seeded.owner.get(seeded.url("/0")).status_code == 422
    assert seeded.owner.get(seeded.url("/abc")).status_code == 422


def test_facets_counts(seeded: Seeded) -> None:
    r = seeded.owner.get(seeded.url("/facets"), WINDOW)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {f["value"]: f["count"] for f in body["levels"]} == dict.fromkeys(LEVELS, 8)
    services = {f["value"]: f["count"] for f in body["services"]}
    assert services == {"api": 14, "worker": 13, "web": 13}
    assert [f["value"] for f in body["services"]] == ["api", "web", "worker"]  # count desc, name
    assert {f["value"]: f["count"] for f in body["environments"]} == {
        "production": 30,
        "staging": 10,
    }
    empty = seeded.owner.get(
        seeded.url("/facets"),
        {"from": iso(T0 + timedelta(hours=2)), "to": iso(T0 + timedelta(hours=3))},
    ).json()
    assert empty["levels"] == empty["services"] == empty["environments"] == []
