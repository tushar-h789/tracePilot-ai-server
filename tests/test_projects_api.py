"""Projects: CRUD, role matrix, archive, pagination and cross-org isolation (real Postgres)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import pytest
from fastapi import FastAPI
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog
from tests.helpers import ApiUser

pytestmark = pytest.mark.integration

ROLES = ("owner", "admin", "member", "viewer")


@dataclass
class Team:
    org_id: str
    users: dict[str, ApiUser]
    outsider: ApiUser

    def __getitem__(self, role: str) -> ApiUser:
        return self.users[role]


@pytest.fixture
def team(app: FastAPI) -> Team:
    owner = ApiUser(app).signup()
    org = owner.create_org("Proj Org")
    users = {"owner": owner}
    for role in ("admin", "member", "viewer"):
        user = ApiUser(app).signup()
        r = owner.post(f"/organizations/{org['id']}/members", {"email": user.email, "role": role})
        assert r.status_code == 201, r.text
        users[role] = user
    return Team(org_id=org["id"], users=users, outsider=ApiUser(app).signup())


def test_create_get_list_project(team: Team) -> None:
    project = team["owner"].create_project(team.org_id, "Checkout API", environment="staging")
    assert project["slug"] == "checkout-api"
    assert project["environment"] == "staging"
    assert project["retention_days"] == 7
    assert project["archived_at"] is None
    assert project["org_id"] == team.org_id
    got = team["viewer"].get(f"/projects/{project['id']}")
    assert got.status_code == 200
    assert got.json()["role"] == "viewer"
    listed = team["member"].get(f"/organizations/{team.org_id}/projects").json()
    assert [p["id"] for p in listed["items"]] == [project["id"]]
    assert listed["next_cursor"] is None


@pytest.mark.parametrize(
    ("role", "status"), [("owner", 201), ("admin", 201), ("member", 403), ("viewer", 403)]
)
def test_create_matrix(team: Team, role: str, status: int) -> None:
    r = team[role].post(f"/organizations/{team.org_id}/projects", {"name": f"By {role}"})
    assert r.status_code == status, r.text


@pytest.mark.parametrize(
    ("role", "status"), [("owner", 200), ("admin", 200), ("member", 403), ("viewer", 403)]
)
def test_update_and_archive_matrix(team: Team, role: str, status: int) -> None:
    project = team["owner"].create_project(team.org_id, "Matrix")
    pid = project["id"]
    assert team[role].patch(f"/projects/{pid}", {"name": "Renamed"}).status_code == status
    assert team[role].post(f"/projects/{pid}/archive").status_code == status
    assert team[role].post(f"/projects/{pid}/restore").status_code == status


def test_read_allowed_for_every_role(team: Team) -> None:
    pid = team["owner"].create_project(team.org_id)["id"]
    for role in ROLES:
        assert team[role].get(f"/projects/{pid}").status_code == 200
        assert team[role].get(f"/organizations/{team.org_id}/projects").status_code == 200


def test_cross_org_project_access_is_404(app: FastAPI, team: Team) -> None:
    pid = team["owner"].create_project(team.org_id)["id"]
    other_owner = team.outsider
    other_org = other_owner.create_org("Other")
    calls: list[Callable[[], Response]] = [
        lambda: other_owner.get(f"/projects/{pid}"),
        lambda: other_owner.patch(f"/projects/{pid}", {"name": "x"}),
        lambda: other_owner.post(f"/projects/{pid}/archive"),
        lambda: other_owner.get(f"/projects/{pid}/ingestion-keys"),
        lambda: other_owner.post(f"/projects/{pid}/ingestion-keys", {"name": "steal"}),
        lambda: other_owner.get(f"/organizations/{team.org_id}/projects"),
        lambda: other_owner.post(f"/organizations/{team.org_id}/projects", {"name": "x"}),
    ]
    for call in calls:
        r = call()
        assert r.status_code == 404, r.text
    # Unknown id looks exactly the same as a foreign one.
    unknown = other_owner.get(f"/projects/{uuid.uuid4()}")
    foreign = other_owner.get(f"/projects/{pid}")
    assert unknown.json()["detail"] == foreign.json()["detail"] == "Project not found."
    # The other org cannot see team projects in its own list either.
    assert other_owner.get(f"/organizations/{other_org['id']}/projects").json()["items"] == []


def test_create_rejects_client_org_id_and_unknown_fields(team: Team) -> None:
    owner = team["owner"]
    r = owner.post(
        f"/organizations/{team.org_id}/projects",
        {"name": "Sneaky", "org_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422
    r = owner.patch(f"/projects/{owner.create_project(team.org_id)['id']}", {"archived_at": None})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "   "},
        {"name": "x" * 101},
        {"name": "ok", "slug": "Bad Slug"},
        {"name": "ok", "slug": "-bad"},
        {"name": "ok", "retention_days": 0},
        {"name": "ok", "retention_days": 31},
        {"name": "ok", "environment": "Prod!"},
    ],
)
def test_create_validation(team: Team, body: dict[str, object]) -> None:
    r = team["owner"].post(f"/organizations/{team.org_id}/projects", body)
    assert r.status_code == 422, r.text


def test_slug_unique_per_org_not_global(team: Team) -> None:
    owner = team["owner"]
    first = owner.create_project(team.org_id, "Payments")
    second = owner.create_project(team.org_id, "Payments")  # derived slug gets a suffix
    assert first["slug"] == "payments"
    assert second["slug"].startswith("payments-")
    assert second["slug"] != "payments"
    explicit = owner.post(
        f"/organizations/{team.org_id}/projects", {"name": "P", "slug": "payments"}
    )
    assert explicit.status_code == 409
    other_org = team.outsider.create_org("Elsewhere")
    same_slug = team.outsider.create_project(other_org["id"], "Payments")
    assert same_slug["slug"] == "payments"
    clash = owner.patch(f"/projects/{second['id']}", {"slug": "payments"})
    assert clash.status_code == 409


def test_update_fields(team: Team) -> None:
    owner = team["owner"]
    pid = owner.create_project(team.org_id, "Old")["id"]
    r = owner.patch(
        f"/projects/{pid}",
        {"name": "  New   Name ", "slug": "new-name", "environment": "dev", "retention_days": 30},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["name"], body["slug"], body["environment"], body["retention_days"]) == (
        "New Name",
        "new-name",
        "dev",
        30,
    )


def test_archive_hides_from_default_list_and_is_read_only(team: Team) -> None:
    owner = team["owner"]
    keep = owner.create_project(team.org_id, "Keep")
    gone = owner.create_project(team.org_id, "Gone")
    archived = owner.post(f"/projects/{gone['id']}/archive")
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert owner.post(f"/projects/{gone['id']}/archive").status_code == 200  # idempotent
    default = owner.get(f"/organizations/{team.org_id}/projects").json()["items"]
    assert [p["id"] for p in default] == [keep["id"]]
    everything = owner.get(
        f"/organizations/{team.org_id}/projects", params={"include_archived": "true"}
    ).json()["items"]
    assert {p["id"] for p in everything} == {keep["id"], gone["id"]}
    assert owner.get(f"/projects/{gone['id']}").status_code == 200
    assert owner.patch(f"/projects/{gone['id']}", {"name": "x"}).status_code == 409
    assert owner.post(f"/projects/{gone['id']}/ingestion-keys", {"name": "k"}).status_code == 409
    restored = owner.post(f"/projects/{gone['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None


def test_keyset_pagination(team: Team) -> None:
    owner = team["owner"]
    created = [owner.create_project(team.org_id, f"P{i}")["id"] for i in range(5)]
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = owner.get(f"/organizations/{team.org_id}/projects", params=params).json()
        seen += [p["id"] for p in page["items"]]
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert seen == list(reversed(created))  # newest first, no gaps or repeats
    bad = owner.get(f"/organizations/{team.org_id}/projects", params={"cursor": "not-a-cursor"})
    assert bad.status_code == 422
    assert (
        owner.get(f"/organizations/{team.org_id}/projects", params={"limit": 101}).status_code
        == 422
    )


def test_project_mutations_are_audited(team: Team, db_session: Session) -> None:
    owner = team["owner"]
    pid = owner.create_project(team.org_id, "Audited")["id"]
    owner.patch(f"/projects/{pid}", {"retention_days": 14})
    owner.post(f"/projects/{pid}/archive")
    owner.post(f"/projects/{pid}/restore")
    team["member"].patch(f"/projects/{pid}", {"name": "nope"})
    actions = list(
        db_session.scalars(
            select(AuditLog.action).where(AuditLog.target_id == pid).order_by(AuditLog.created_at)
        )
    )
    assert actions == [
        "project.created",
        "project.updated",
        "project.archived",
        "project.restored",
        "authz.permission_denied",
    ]


def test_project_routes_require_auth_and_csrf(app: FastAPI, team: Team) -> None:
    pid = team["owner"].create_project(team.org_id)["id"]
    anonymous = ApiUser(app)
    assert anonymous.get(f"/projects/{pid}").status_code == 401
    r = team["owner"].post(
        f"/organizations/{team.org_id}/projects", {"name": "x"}, headers={"X-CSRF-Token": "bad"}
    )
    assert r.status_code == 403
