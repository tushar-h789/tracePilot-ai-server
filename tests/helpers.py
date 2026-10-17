"""Test helpers: a browser-like API user (cookie jar + automatic CSRF header)."""

from __future__ import annotations

import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

API = "/api/v1"
DEFAULT_PASSWORD = "correct-horse-battery-9"  # test-only placeholder
_counter = itertools.count(1)


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}{next(_counter)}@example.com"


class ApiUser:
    """Wraps a TestClient with its own cookie jar, like one browser profile."""

    def __init__(self, app: FastAPI, email: str | None = None, password: str = DEFAULT_PASSWORD):
        self.client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
        self.email = email or unique_email()
        self.password = password

    # --- CSRF -------------------------------------------------------------------------------
    def csrf(self) -> str:
        token = self.client.cookies.get("tp_csrf")
        if not token:
            response = self.client.get(f"{API}/auth/csrf")
            assert response.status_code == 200
            token = response.json()["csrf_token"]
        return str(token)

    def _headers(self, extra: dict[str, str] | None, csrf: bool) -> dict[str, str]:
        headers = dict(extra or {})
        if csrf and "X-CSRF-Token" not in headers:
            headers["X-CSRF-Token"] = self.csrf()
        return headers

    def get(self, path: str, params: dict[str, Any] | None = None) -> Response:
        response: Response = self.client.get(f"{API}{path}", params=params)
        return response

    def post(
        self,
        path: str,
        json: Any = None,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Response:
        response: Response = self.client.post(
            f"{API}{path}", json=json, headers=self._headers(headers, csrf)
        )
        return response

    def patch(self, path: str, json: Any = None, *, csrf: bool = True) -> Response:
        response: Response = self.client.patch(
            f"{API}{path}", json=json, headers=self._headers(None, csrf)
        )
        return response

    def delete(self, path: str, *, csrf: bool = True) -> Response:
        response: Response = self.client.delete(f"{API}{path}", headers=self._headers(None, csrf))
        return response

    # --- flows ------------------------------------------------------------------------------
    def register(self, **overrides: Any) -> Response:
        body = {"email": self.email, "password": self.password, **overrides}
        return self.post("/auth/register", body)

    def login(self, password: str | None = None, email: str | None = None) -> Response:
        return self.post(
            "/auth/login",
            {"email": email or self.email, "password": password or self.password},
        )

    def signup(self) -> ApiUser:
        assert self.register().status_code == 202
        response = self.login()
        assert response.status_code == 200, response.text
        return self

    @property
    def session_cookie(self) -> str | None:
        value = self.client.cookies.get("tp_session")
        return str(value) if value else None

    def create_org(self, name: str = "Acme") -> dict[str, Any]:
        response = self.post("/organizations", {"name": name})
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    def me_id(self) -> str:
        return str(self.get("/auth/me").json()["user"]["id"])

    # --- Phase 3 ----------------------------------------------------------------------------
    def create_project(self, org_id: str, name: str = "Checkout", **extra: Any) -> dict[str, Any]:
        response = self.post(f"/organizations/{org_id}/projects", {"name": name, **extra})
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data

    def create_key(self, project_id: str, name: str = "ci") -> dict[str, Any]:
        response = self.post(f"/projects/{project_id}/ingestion-keys", {"name": name})
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data


def ingest(
    app: FastAPI,
    key: str | None,
    body: Any = None,
    *,
    raw: bytes | None = None,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> Response:
    """POST /ingest/logs from a fresh client (no cookie jar), like an SDK would."""
    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    if cookies:
        for name, value in cookies.items():
            client.cookies.set(name, value)
    sent_headers = {"Content-Type": "application/json", **(headers or {})}
    if key is not None:
        sent_headers["Authorization"] = f"Bearer {key}"
    content = raw if raw is not None else json.dumps(body).encode()
    response: Response = client.post(f"{API}/ingest/logs", content=content, headers=sent_headers)
    return response


def make_event(index: int = 0, **overrides: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": str(uuid.uuid4()),
        "timestamp": (datetime.now(UTC) - timedelta(seconds=index)).isoformat(),
        "level": "info",
        "service_name": "checkout",
        "message": f"GET /api/cart -> 200 ({index})",
        "trace_id": uuid.uuid4().hex,
        "request_id": f"req-{index}",
        "duration_ms": 12.5,
        "http_method": "GET",
        "http_path": "/api/cart",
        "http_status": 200,
        "attributes": {"region": "local", "synthetic": True},
    }
    event.update(overrides)
    return event


# --- Phase 4 ------------------------------------------------------------------------------------
def insert_events(db: Any, project_id: str, rows: list[dict[str, Any]]) -> list[int]:
    """Insert log_events directly (exact timestamps, any age) and return their ids in order."""
    from app.models import LogEvent

    models = []
    for row in rows:
        values: dict[str, Any] = {
            "project_id": uuid.UUID(project_id),
            "event_id": uuid.uuid4(),
            "level": "info",
            "service": "api",
            "environment": "production",
            "message": "hello",
            "attributes": {},
            "is_synthetic": True,
            **row,
        }
        models.append(LogEvent(**values))
    db.add_all(models)
    db.commit()
    return [m.id for m in models]


def iso(value: datetime) -> str:
    return value.isoformat()
