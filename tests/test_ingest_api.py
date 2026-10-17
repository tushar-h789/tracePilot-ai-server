"""POST /ingest/logs: key auth, strict contract, limits, idempotency, rate limits, tenancy."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import JsonFormatter
from app.models import IngestionKey, LogEvent
from tests.helpers import ApiUser, ingest, make_event

pytestmark = pytest.mark.integration


class Tenant:
    def __init__(self, app: FastAPI, name: str) -> None:
        self.owner = ApiUser(app).signup()
        self.org = self.owner.create_org(f"{name} Org")
        self.project = self.owner.create_project(self.org["id"], f"{name} Project")
        self.key = self.owner.create_key(self.project["id"])
        self.secret: str = self.key["secret"]

    @property
    def project_id(self) -> str:
        return str(self.project["id"])


@pytest.fixture
def tenant(app: FastAPI) -> Tenant:
    return Tenant(app, "Alpha")


def _count(db: Session, project_id: str) -> int:
    return int(
        db.scalar(select(func.count()).where(LogEvent.project_id == uuid.UUID(project_id))) or 0
    )


def test_ingest_success_writes_rows(app: FastAPI, tenant: Tenant, db_session: Session) -> None:
    events = [make_event(i) for i in range(3)]
    events[1].update(level="error", http_status=500, environment="demo", duration_ms=1234.0)
    r = ingest(app, tenant.secret, {"events": events})
    assert r.status_code == 202, r.text
    assert r.json() == {"received": 3, "accepted": 3, "duplicates": 0, "rejected": 0}
    assert r.headers["cache-control"] == "no-store"
    rows = list(db_session.scalars(select(LogEvent).order_by(LogEvent.id)))
    assert len(rows) == 3
    row = next(r for r in rows if str(r.event_id) == events[1]["event_id"])
    assert row.project_id == uuid.UUID(tenant.project_id)
    assert row.level == "error"
    assert row.http_status == 500
    assert row.environment == "demo"
    assert row.service == "checkout"
    assert row.duration_ms == 1234.0
    assert row.ts.utcoffset() == timedelta(0)
    assert row.is_synthetic is True
    assert row.ingestion_key_id == uuid.UUID(tenant.key["key"]["id"])
    default_env = next(r for r in rows if str(r.event_id) == events[0]["event_id"])
    assert default_env.environment == "production"  # project default


def test_timestamps_are_normalized_to_utc(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    plus6 = (datetime.now(UTC) - timedelta(minutes=1)).astimezone(timezone(timedelta(hours=6)))
    event = make_event(timestamp=plus6.isoformat())
    assert ingest(app, tenant.secret, {"events": [event]}).status_code == 202
    stored = db_session.scalars(select(LogEvent)).one()
    assert stored.ts == plus6.astimezone(UTC)
    assert stored.ts.utcoffset() == timedelta(0)


def test_duplicates_within_and_across_batches(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    a, b = make_event(1), make_event(2)
    same_batch = ingest(app, tenant.secret, {"events": [a, a, b]})
    assert same_batch.json() == {"received": 3, "accepted": 2, "duplicates": 1, "rejected": 0}
    replay = ingest(app, tenant.secret, {"events": [a, b]})
    assert replay.json() == {"received": 2, "accepted": 0, "duplicates": 2, "rejected": 0}
    assert _count(db_session, tenant.project_id) == 2


def test_event_ids_are_scoped_per_project(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    other = Tenant(app, "Beta")
    event = make_event()
    assert ingest(app, tenant.secret, {"events": [event]}).json()["accepted"] == 1
    assert ingest(app, other.secret, {"events": [event]}).json()["accepted"] == 1
    assert _count(db_session, tenant.project_id) == 1
    assert _count(db_session, other.project_id) == 1


def test_payload_project_id_cannot_target_another_tenant(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    victim = Tenant(app, "Victim")
    bad_event = make_event(project_id=victim.project_id)
    r = ingest(app, tenant.secret, {"events": [bad_event]})
    assert r.status_code == 422
    assert r.json()["errors"][0]["type"] == "extra_forbidden"
    r = ingest(app, tenant.secret, {"project_id": victim.project_id, "events": [make_event()]})
    assert r.status_code == 422
    assert _count(db_session, victim.project_id) == 0
    assert _count(db_session, tenant.project_id) == 0


@pytest.mark.parametrize(
    ("headers", "key_factory"),
    [
        ({}, lambda t: None),
        ({}, lambda t: "tp_000000000000_" + "A" * 43),  # well-formed, unknown
        ({}, lambda t: t.secret[:-1] + ("B" if t.secret[-1] != "B" else "C")),  # wrong secret
        ({}, lambda t: "not-a-key"),
        ({"Authorization": "Basic Zm9vOmJhcg=="}, lambda t: None),
    ],
)
def test_bad_keys_get_generic_401(
    app: FastAPI,
    tenant: Tenant,
    headers: dict[str, str],
    key_factory: Callable[[Tenant], str | None],
) -> None:
    r = ingest(app, key_factory(tenant), {"events": [make_event()]}, headers=headers)
    assert r.status_code == 401
    assert r.json()["detail"] == "A valid ingestion key is required."


def test_cookie_only_request_is_rejected(app: FastAPI, tenant: Tenant) -> None:
    session = tenant.owner.session_cookie
    csrf = tenant.owner.client.cookies.get("tp_csrf")
    assert session
    assert csrf
    r = ingest(
        app,
        None,
        {"events": [make_event()]},
        cookies={"tp_session": session, "tp_csrf": csrf},
        headers={"X-CSRF-Token": str(csrf)},
    )
    assert r.status_code == 401


def test_archived_project_rejects_ingestion(app: FastAPI, tenant: Tenant) -> None:
    tenant.owner.post(f"/projects/{tenant.project_id}/archive")
    assert ingest(app, tenant.secret, {"events": [make_event()]}).status_code == 401
    tenant.owner.post(f"/projects/{tenant.project_id}/restore")
    assert ingest(app, tenant.secret, {"events": [make_event()]}).status_code == 202


def test_oversized_body_is_413_before_parsing(make_app: Callable[..., Any]) -> None:
    app = make_app(ingest_max_body_bytes=2048)
    tenant = Tenant(app, "Big")
    big = {"events": [make_event(message="x" * 4000)]}
    r = ingest(app, tenant.secret, big)
    assert r.status_code == 413
    assert r.json()["code"] == "payload_too_large"
    # Not JSON at all, still 413 (size checked first).
    assert ingest(app, tenant.secret, raw=b"{" * 4096).status_code == 413


def test_default_body_limit_is_one_mebibyte(app: FastAPI, tenant: Tenant) -> None:
    payload = b'{"events": [' + b" " * (1_048_576) + b"]}"
    assert ingest(app, tenant.secret, raw=payload).status_code == 413


def test_streamed_body_without_content_length_is_capped(make_app: Callable[..., Any]) -> None:
    from fastapi.testclient import TestClient

    app = make_app(ingest_max_body_bytes=1024)
    tenant = Tenant(app, "Stream")

    def chunks() -> Any:
        for _ in range(10):
            yield b" " * 512

    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    r = client.post(
        "/api/v1/ingest/logs",
        content=chunks(),
        headers={"Authorization": f"Bearer {tenant.secret}", "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_too_many_events_is_422(app: FastAPI, tenant: Tenant, db_session: Session) -> None:
    events = [make_event(i % 50) for i in range(501)]
    r = ingest(app, tenant.secret, {"events": events})
    assert r.status_code == 422
    assert r.json()["errors"][0]["loc"] == ["body", "events"]
    assert _count(db_session, tenant.project_id) == 0
    ok = ingest(app, tenant.secret, {"events": events[:500]})
    assert ok.status_code == 202
    assert ok.json()["accepted"] == 500


@pytest.mark.parametrize(
    ("overrides", "loc_tail"),
    [
        ({"timestamp": "2026-10-17T10:00:00"}, "timestamp"),  # naive
        ({"timestamp": "yesterday"}, "timestamp"),
        ({"timestamp": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}, "timestamp"),
        ({"timestamp": (datetime.now(UTC) - timedelta(days=8)).isoformat()}, "timestamp"),
        ({"level": "warn"}, "level"),
        ({"level": "fatal"}, "level"),
        ({"event_id": "not-a-uuid"}, "event_id"),
        ({"service_name": ""}, "service_name"),
        ({"service_name": "bad name"}, "service_name"),
        ({"message": ""}, "message"),
        ({"message": "x" * 8193}, "message"),
        ({"duration_ms": -1}, "duration_ms"),
        ({"http_status": 99}, "http_status"),
        ({"http_status": 600}, "http_status"),
        ({"http_method": "TRACE"}, "http_method"),
        ({"attributes": [1, 2]}, "attributes"),
        ({"attributes": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}}, "attributes"),
        ({"attributes": {f"k{i}": i for i in range(129)}}, "attributes"),
        ({"attributes": {"blob": "x" * 17_000}}, "attributes"),
        ({"message": "nul\u0000byte"}, "message"),
        ({"unexpected": True}, "unexpected"),
    ],
)
def test_invalid_event_rejects_whole_batch(
    app: FastAPI, tenant: Tenant, db_session: Session, overrides: dict[str, Any], loc_tail: str
) -> None:
    good = make_event(0)
    bad = make_event(1)
    bad.update(overrides)
    r = ingest(app, tenant.secret, {"events": [good, bad]})
    assert r.status_code == 422, r.text
    errors = r.json()["errors"]
    assert errors[0]["loc"][:3] == ["body", "events", 1]
    assert loc_tail in [str(part) for part in errors[0]["loc"]]
    assert "input" not in errors[0]
    assert _count(db_session, tenant.project_id) == 0  # all-or-nothing


def test_validation_errors_do_not_echo_payload(app: FastAPI, tenant: Tenant) -> None:
    marker = "super-secret-value-should-not-echo"
    r = ingest(app, tenant.secret, {"events": [make_event(level=marker)]})
    assert r.status_code == 422
    assert marker not in r.text


@pytest.mark.parametrize(
    ("raw", "content_type"),
    [
        (b"not json", "application/json"),
        (b"[]", "application/json"),
        (b'{"events": []}', "application/json"),
        (json.dumps({"events": [make_event()]}).encode(), "text/plain"),
    ],
)
def test_malformed_requests(app: FastAPI, tenant: Tenant, raw: bytes, content_type: str) -> None:
    r = ingest(app, tenant.secret, raw=raw, headers={"Content-Type": content_type})
    assert r.status_code == (415 if content_type != "application/json" else 422)


def test_compressed_bodies_are_refused(app: FastAPI, tenant: Tenant) -> None:
    r = ingest(app, tenant.secret, {"events": [make_event()]}, headers={"Content-Encoding": "gzip"})
    assert r.status_code == 415


def test_sensitive_attributes_are_redacted_at_ingest(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    leaked = "tp_0123456789ab_" + "Z" * 43
    event = make_event(
        message=f"connecting with key {leaked}",
        attributes={"password": "hunter2", "nested": {"api_key": "abc"}, "region": "eu"},
    )
    assert ingest(app, tenant.secret, {"events": [event]}).status_code == 202
    row = db_session.scalars(select(LogEvent)).one()
    assert row.attributes == {
        "password": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]"},
        "region": "eu",
    }
    assert "Z" * 43 not in row.message
    assert "tp_0123456789ab_[REDACTED]" in row.message
    assert row.is_synthetic is False


def test_per_key_rate_limit_returns_429_with_retry_after(make_app: Callable[..., Any]) -> None:
    app = make_app(ingest_rate_limit_requests_per_key=2)
    tenant = Tenant(app, "Rate")
    assert ingest(app, tenant.secret, {"events": [make_event()]}).status_code == 202
    assert ingest(app, tenant.secret, {"events": [make_event()]}).status_code == 202
    r = ingest(app, tenant.secret, {"events": [make_event()]})
    assert r.status_code == 429
    assert 1 <= int(r.headers["retry-after"]) <= 60
    # Another key of the same project has its own request budget.
    other = tenant.owner.create_key(tenant.project_id, "second")
    assert ingest(app, other["secret"], {"events": [make_event()]}).status_code == 202


def test_per_project_event_rate_limit(make_app: Callable[..., Any]) -> None:
    app = make_app(ingest_rate_limit_events_per_project=5)
    tenant = Tenant(app, "Events")
    assert (
        ingest(app, tenant.secret, {"events": [make_event(i) for i in range(5)]}).status_code == 202
    )
    r = ingest(app, tenant.secret, {"events": [make_event(9)]})
    assert r.status_code == 429
    assert "retry-after" in r.headers


def test_redis_down_fails_closed(make_app: Callable[..., Any], tenant: Tenant) -> None:
    broken = make_app(redis_url="redis://127.0.0.1:1/0", health_check_timeout_seconds=0.2)
    r = ingest(broken, tenant.secret, {"events": [make_event()]})
    assert r.status_code == 503
    assert r.json()["detail"] == "Ingestion is temporarily unavailable."


def test_last_used_at_is_updated_and_throttled(
    app: FastAPI, tenant: Tenant, db_session: Session
) -> None:
    key_id = uuid.UUID(tenant.key["key"]["id"])
    assert db_session.get(IngestionKey, key_id).last_used_at is None  # type: ignore[union-attr]
    ingest(app, tenant.secret, {"events": [make_event()]})
    db_session.expire_all()
    first = db_session.get(IngestionKey, key_id).last_used_at  # type: ignore[union-attr]
    assert first is not None
    ingest(app, tenant.secret, {"events": [make_event()]})
    db_session.expire_all()
    assert db_session.get(IngestionKey, key_id).last_used_at == first  # type: ignore[union-attr]


def test_ingest_ignores_csrf_and_cors_does_not_allow_authorization(app: FastAPI) -> None:
    from fastapi.testclient import TestClient

    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    preflight = client.options(
        "/api/v1/ingest/logs",
        headers={
            "Origin": "http://localhost:3100",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 400  # Authorization is not an allowed CORS header


def test_logs_contain_no_key_material(
    app: FastAPI, tenant: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    ingest(app, tenant.secret, {"events": [make_event()]})
    ingest(app, tenant.secret[:-2] + "xx", {"events": [make_event()]})
    rotated = tenant.owner.post(
        f"/projects/{tenant.project_id}/ingestion-keys/{tenant.key['key']['id']}/rotate", {}
    ).json()
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    assert "ingest_batch" in rendered
    assert "ingest_auth_failed" in rendered
    for secret in (tenant.secret, rotated["secret"]):
        assert secret.split("_", 2)[2] not in rendered
    assert tenant.key["key"]["prefix"] in rendered  # the public prefix is fine to log
