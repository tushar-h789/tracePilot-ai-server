"""Authorization sweep over EVERY org- and project-scoped route, enumerated from the app.

The route list comes from the generated OpenAPI document (which FastAPI builds from the router),
so a newly added `/organizations/{org_id}/...` or `/projects/{project_id}/...` route is covered
automatically. Each path parameter is filled with a REAL id from the victim tenant, so a 404 can
only come from the membership check, never from a missing resource.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import Response
from sqlalchemy.orm import Session

from app.main import create_app
from tests.helpers import API, ApiUser, insert_events

pytestmark = pytest.mark.integration


def _scoped_routes() -> list[tuple[str, str]]:
    os.environ.setdefault("APP_ENV", "test")
    spec = create_app().openapi()
    routes = []
    for path, ops in spec["paths"].items():
        if "{org_id}" in path or "{project_id}" in path:
            routes += [(method.upper(), path.removeprefix(API)) for method in sorted(ops)]
    return routes


SCOPED = _scoped_routes()
READS = [(m, p) for m, p in SCOPED if m == "GET"]
# Reads with a stricter role rule (tested in their own modules); viewers get 403 there.
PRIVILEGED_READS = {
    "/organizations/{org_id}/audit-logs",  # admin+
    "/projects/{project_id}/ingestion-keys",  # member+
}
VIEWER_READS = [(m, p) for m, p in READS if p not in PRIVILEGED_READS]


def test_inventory_covers_phase4_routes() -> None:
    paths = {p for _, p in SCOPED}
    for expected in (
        "/projects/{project_id}/logs",
        "/projects/{project_id}/logs/facets",
        "/projects/{project_id}/logs/{log_id}",
        "/projects/{project_id}/metrics/overview",
        "/projects/{project_id}/metrics/timeseries",
        "/projects/{project_id}/metrics/services",
        "/projects/{project_id}/metrics/recent-errors",
        "/organizations/{org_id}/metrics/summary",
        "/organizations/{org_id}/metrics/timeseries",
        "/organizations/{org_id}",
    ):
        assert expected in paths
    assert len(SCOPED) >= 25


@dataclass
class Victim:
    owner: ApiUser
    viewer: ApiUser
    outsider: ApiUser
    ids: dict[str, str]

    def url(self, path: str) -> str:
        for name, value in self.ids.items():
            path = path.replace("{" + name + "}", value)
        assert "{" not in path, path
        return path


@pytest.fixture
def victim(app: FastAPI, db_session: Session) -> Victim:
    owner = ApiUser(app).signup()
    org = owner.create_org("Victim Org")
    project = owner.create_project(org["id"], "Victim Project")
    key = owner.create_key(project["id"])
    viewer = ApiUser(app).signup()
    added = owner.post(
        f"/organizations/{org['id']}/members", {"email": viewer.email, "role": "viewer"}
    )
    assert added.status_code == 201, added.text
    [log_id] = insert_events(db_session, project["id"], [{"ts": datetime.now(UTC)}])
    outsider = ApiUser(app).signup()
    outsider.create_org("Outsider Org")  # an owner elsewhere still gets nothing here
    ids = {
        "org_id": org["id"],
        "project_id": project["id"],
        "key_id": key["key"]["id"],
        "user_id": owner.me_id(),
        "log_id": str(log_id),
    }
    return Victim(owner, viewer, outsider, ids)


def _call(user: ApiUser, method: str, url: str) -> Response:
    if method == "GET":
        return user.get(url)
    if method == "DELETE":
        return user.delete(url)
    if method == "PATCH":
        return user.patch(url, {"name": "pwned", "role": "owner"})
    return user.post(url, {"name": "pwned", "email": "x@example.com", "role": "owner"})


@pytest.mark.parametrize(("method", "path"), SCOPED, ids=[f"{m} {p}" for m, p in SCOPED])
def test_non_member_gets_404_everywhere(victim: Victim, method: str, path: str) -> None:
    r = _call(victim.outsider, method, victim.url(path))
    assert r.status_code == 404, r.text
    assert r.headers["content-type"].startswith("application/problem+json")


@pytest.mark.parametrize(("method", "path"), SCOPED, ids=[f"{m} {p}" for m, p in SCOPED])
def test_anonymous_gets_401_everywhere(
    app: FastAPI, victim: Victim, method: str, path: str
) -> None:
    anonymous = ApiUser(app)  # has a CSRF token, but no session
    r = _call(anonymous, method, victim.url(path))
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(("method", "path"), VIEWER_READS, ids=[p for _, p in VIEWER_READS])
def test_viewer_can_read_everything(victim: Victim, method: str, path: str) -> None:
    r = victim.viewer.get(victim.url(path))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("path", sorted(PRIVILEGED_READS))
def test_viewer_gets_403_on_privileged_reads(victim: Victim, path: str) -> None:
    assert path in {p for _, p in READS}
    assert victim.viewer.get(victim.url(path)).status_code == 403


@pytest.mark.parametrize(("method", "path"), VIEWER_READS, ids=[p for _, p in VIEWER_READS])
def test_archived_project_stays_readable(victim: Victim, method: str, path: str) -> None:
    r = victim.owner.post(f"/projects/{victim.ids['project_id']}/archive")
    assert r.status_code == 200, r.text
    assert victim.viewer.get(victim.url(path)).status_code == 200


def test_org_rename_is_owner_admin_only_and_audited(app: FastAPI, victim: Victim) -> None:
    org_id = victim.ids["org_id"]
    assert (
        victim.viewer.patch(f"/organizations/{org_id}", {"name": "Viewer Org"}).status_code == 403
    )
    admin = ApiUser(app).signup()
    victim.owner.post(f"/organizations/{org_id}/members", {"email": admin.email, "role": "admin"})
    slug = victim.owner.get(f"/organizations/{org_id}").json()["slug"]
    r = admin.patch(f"/organizations/{org_id}", {"name": "  Renamed   Org "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed Org"
    assert body["slug"] == slug  # slug is stable
    for bad in ({"name": ""}, {"name": "   "}, {"name": "x" * 101}, {"name": "ok", "slug": "x"}):
        assert admin.patch(f"/organizations/{org_id}", bad).status_code == 422, bad
    logs = victim.owner.get(f"/organizations/{org_id}/audit-logs").json()["items"]
    renamed = [entry for entry in logs if entry["action"] == "org.updated"]
    assert len(renamed) == 1
    assert renamed[0]["metadata"]["name"] == {"from": "Victim Org", "to": "Renamed Org"}
    # Same name again: no new audit entry.
    assert admin.patch(f"/organizations/{org_id}", {"name": "Renamed Org"}).status_code == 200
    logs = victim.owner.get(f"/organizations/{org_id}/audit-logs").json()["items"]
    assert len([e for e in logs if e["action"] == "org.updated"]) == 1
