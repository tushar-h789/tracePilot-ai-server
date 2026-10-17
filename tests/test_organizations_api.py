"""Organizations, membership, RBAC matrix and tenant isolation (real Postgres + Redis)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.net import ClientInfo
from app.models import AuditLog, Organization, OrganizationMember, User
from app.repositories.organizations import OrganizationRepository
from app.services.audit import AuditService
from app.services.organizations import OrganizationService
from tests.helpers import ApiUser

pytestmark = pytest.mark.integration


@dataclass
class Org:
    id: str
    owner: ApiUser
    admin: ApiUser
    member: ApiUser
    viewer: ApiUser
    outsider: ApiUser

    def by_role(self, role: str) -> ApiUser:
        return {
            "owner": self.owner,
            "admin": self.admin,
            "member": self.member,
            "viewer": self.viewer,
        }[role]

    @property
    def base(self) -> str:
        return f"/organizations/{self.id}"


def _add(owner: ApiUser, org_id: str, user: ApiUser, role: str) -> None:
    r = owner.post(f"/organizations/{org_id}/members", {"email": user.email, "role": role})
    assert r.status_code == 201, r.text


@pytest.fixture
def org(app: FastAPI) -> Org:
    owner = ApiUser(app).signup()
    created = owner.create_org("Acme Observability")
    users = {role: ApiUser(app).signup() for role in ("admin", "member", "viewer")}
    for role, user in users.items():
        _add(owner, created["id"], user, role)
    return Org(
        id=created["id"],
        owner=owner,
        admin=users["admin"],
        member=users["member"],
        viewer=users["viewer"],
        outsider=ApiUser(app).signup(),
    )


# --- organizations ---------------------------------------------------------------------------
def test_create_list_get_organization(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    assert user.get("/organizations").json() == {"items": []}
    created = user.create_org("  Acme   Corp ")
    assert created["name"] == "Acme Corp"
    assert created["role"] == "owner"
    assert created["slug"].startswith("acme-corp-")
    listed = user.get("/organizations").json()["items"]
    assert [o["id"] for o in listed] == [created["id"]]
    got = user.get(f"/organizations/{created['id']}")
    assert got.status_code == 200
    assert got.json()["role"] == "owner"
    assert "org.created" in db_session.scalars(select(AuditLog.action)).all()


def test_create_org_rejects_mass_assignment_and_blank(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    r = user.post("/organizations", {"name": "X", "owner_id": str(uuid.uuid4())})
    assert r.status_code == 422
    assert user.post("/organizations", {"name": "   "}).status_code == 422
    assert user.post("/organizations", {"name": "x" * 101}).status_code == 422


def test_organization_endpoints_require_auth(app: FastAPI) -> None:
    anon = ApiUser(app)
    assert anon.get("/organizations").status_code == 401
    assert anon.post("/organizations", {"name": "X"}).status_code == 401
    assert anon.get(f"/organizations/{uuid.uuid4()}").status_code == 401


# --- tenant isolation ------------------------------------------------------------------------
def test_cross_tenant_access_returns_404(org: Org) -> None:
    eve = org.outsider
    eve_org = eve.create_org("Eve Corp")
    target = org.member.me_id()
    calls = [
        eve.get(org.base),
        eve.get(f"{org.base}/members"),
        eve.get(f"{org.base}/audit-logs"),
        eve.post(f"{org.base}/members", {"email": eve.email, "role": "owner"}),  # add self
        eve.patch(f"{org.base}/members/{target}", {"role": "viewer"}),
        eve.delete(f"{org.base}/members/{target}"),
    ]
    for response in calls:
        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "Organization not found."
    # Indistinguishable from an org that does not exist.
    missing = eve.get(f"/organizations/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == calls[0].json()["detail"]
    # Eve still only sees her own org; Acme unchanged.
    assert [o["id"] for o in eve.get("/organizations").json()["items"]] == [eve_org["id"]]
    assert len(org.owner.get(f"{org.base}/members").json()["items"]) == 4


def test_owner_of_other_org_cannot_touch_members_via_own_org(org: Org) -> None:
    """User A (owner of Org B) cannot use Org B's id to modify an Org A membership."""
    eve = org.outsider
    eve_org = eve.create_org("Eve Corp")
    acme_member = org.member.me_id()
    r = eve.patch(f"/organizations/{eve_org['id']}/members/{acme_member}", {"role": "viewer"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Member not found."
    r = eve.delete(f"/organizations/{eve_org['id']}/members/{acme_member}")
    assert r.status_code == 404


def test_malformed_org_id_is_422(org: Org) -> None:
    assert org.owner.get("/organizations/not-a-uuid").status_code == 422


# --- RBAC matrix -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("role", "status"), [("owner", 200), ("admin", 200), ("member", 200), ("viewer", 200)]
)
def test_any_member_can_read_org_and_members(org: Org, role: str, status: int) -> None:
    user = org.by_role(role)
    assert user.get(org.base).status_code == status
    members = user.get(f"{org.base}/members")
    assert members.status_code == status
    assert {m["role"] for m in members.json()["items"]} == {"owner", "admin", "member", "viewer"}


@pytest.mark.parametrize(
    ("actor", "new_role", "status"),
    [
        ("owner", "member", 201),
        ("owner", "owner", 201),
        ("admin", "member", 201),
        ("admin", "admin", 201),
        ("admin", "owner", 403),
        ("member", "viewer", 403),
        ("viewer", "viewer", 403),
    ],
)
def test_add_member_matrix(app: FastAPI, org: Org, actor: str, new_role: str, status: int) -> None:
    newcomer = ApiUser(app).signup()
    r = org.by_role(actor).post(f"{org.base}/members", {"email": newcomer.email, "role": new_role})
    assert r.status_code == status, r.text
    visible = newcomer.get("/organizations").json()["items"]
    assert len(visible) == (1 if status == 201 else 0)


@pytest.mark.parametrize(
    ("actor", "target", "new_role", "status"),
    [
        ("owner", "member", "admin", 200),
        ("owner", "admin", "owner", 200),
        ("admin", "member", "viewer", 200),
        ("admin", "viewer", "admin", 200),
        ("admin", "member", "owner", 403),  # cannot grant owner
        ("admin", "owner", "member", 403),  # cannot modify owner
        ("admin", "admin", "owner", 403),  # cannot raise own role
        ("member", "viewer", "member", 403),
        ("member", "member", "admin", 403),  # cannot raise own role
        ("viewer", "member", "viewer", 403),
    ],
)
def test_change_role_matrix(org: Org, actor: str, target: str, new_role: str, status: int) -> None:
    target_id = org.by_role(target).me_id()
    r = org.by_role(actor).patch(f"{org.base}/members/{target_id}", {"role": new_role})
    assert r.status_code == status, r.text
    current = {
        m["user_id"]: m["role"] for m in org.owner.get(f"{org.base}/members").json()["items"]
    }
    assert current[target_id] == (new_role if status == 200 else target)


@pytest.mark.parametrize(
    ("actor", "target", "status"),
    [
        ("owner", "admin", 204),
        ("owner", "viewer", 204),
        ("admin", "member", 204),
        ("admin", "viewer", 204),
        ("admin", "owner", 403),
        ("member", "viewer", 403),
        ("viewer", "member", 403),
    ],
)
def test_remove_member_matrix(org: Org, actor: str, target: str, status: int) -> None:
    target_user = org.by_role(target)
    target_id = target_user.me_id()
    r = org.by_role(actor).delete(f"{org.base}/members/{target_id}")
    assert r.status_code == status, r.text
    if status == 204:
        assert target_user.get(org.base).status_code == 404  # access gone immediately


def test_last_owner_is_protected(app: FastAPI, org: Org) -> None:
    owner_id = org.owner.me_id()
    r = org.owner.patch(f"{org.base}/members/{owner_id}", {"role": "admin"})
    assert r.status_code == 409
    assert "at least one owner" in r.json()["detail"]
    assert org.owner.delete(f"{org.base}/members/{owner_id}").status_code == 409
    # With a second owner the first may step down.
    second = org.admin
    assert (
        org.owner.patch(f"{org.base}/members/{second.me_id()}", {"role": "owner"}).status_code
        == 200
    )
    assert org.owner.patch(f"{org.base}/members/{owner_id}", {"role": "admin"}).status_code == 200
    # Now the former owner (admin) cannot touch the remaining owner.
    assert org.owner.delete(f"{org.base}/members/{second.me_id()}").status_code == 403


def test_member_mutations_validate_input(app: FastAPI, org: Org) -> None:
    base = f"{org.base}/members"
    assert org.owner.post(base, {"email": "nobody@example.com"}).status_code == 404
    assert org.owner.post(base, {"email": org.member.email}).status_code == 409
    assert org.owner.post(base, {"email": org.member.email, "role": "god"}).status_code == 422
    r = org.owner.post(base, {"email": org.member.email, "role": "member", "org_id": "x"})
    assert r.status_code == 422
    assert org.owner.patch(f"{base}/{uuid.uuid4()}", {"role": "member"}).status_code == 404
    assert org.owner.delete(f"{base}/{uuid.uuid4()}").status_code == 404
    same = org.owner.patch(f"{base}/{org.member.me_id()}", {"role": "member"})
    assert same.status_code == 200


def test_member_add_is_case_insensitive(app: FastAPI, org: Org) -> None:
    newcomer = ApiUser(app).signup()
    r = org.owner.post(f"{org.base}/members", {"email": newcomer.email.upper()})
    assert r.status_code == 201
    assert r.json()["role"] == "member"


def test_audit_log_access_and_contents(org: Org, db_session: Session) -> None:
    assert (
        org.member.patch(f"{org.base}/members/{org.viewer.me_id()}", {"role": "admin"}).status_code
        == 403
    )
    assert org.member.get(f"{org.base}/audit-logs").status_code == 403
    assert org.viewer.get(f"{org.base}/audit-logs").status_code == 403
    r = org.owner.get(f"{org.base}/audit-logs", params={"limit": 200})
    assert r.status_code == 200
    actions = [e["action"] for e in r.json()["items"]]
    assert "org.created" in actions
    assert actions.count("org.member_added") == 3
    assert "authz.permission_denied" in actions
    assert org.admin.get(f"{org.base}/audit-logs").status_code == 200
    denied = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "authz.permission_denied")
    ).all()
    assert all(row.details.get("role") in {"member", "viewer"} for row in denied)
    for row in db_session.scalars(select(AuditLog)).all():
        text = str(row.details).lower()
        assert "password" not in text
        assert "token" not in text


def test_role_change_audit_records_transition(org: Org) -> None:
    target = org.member.me_id()
    assert org.owner.patch(f"{org.base}/members/{target}", {"role": "admin"}).status_code == 200
    assert org.owner.delete(f"{org.base}/members/{target}").status_code == 204
    events = org.owner.get(f"{org.base}/audit-logs").json()["items"]
    changed = next(e for e in events if e["action"] == "org.member_role_changed")
    assert changed["metadata"] == {"from": "member", "to": "admin"}
    assert changed["target_id"] == target
    assert any(e["action"] == "org.member_removed" for e in events)


# --- transactions ----------------------------------------------------------------------------
def test_org_create_is_atomic(
    app: FastAPI, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the owner membership insert fails, the organization insert is rolled back too."""
    user = ApiUser(app).signup()

    def boom(self: OrganizationRepository, **_: Any) -> OrganizationMember:
        raise RuntimeError("simulated failure after the organization row was flushed")

    monkeypatch.setattr(OrganizationRepository, "add_member", boom)
    r = user.post("/organizations", {"name": "Doomed"})
    assert r.status_code == 500
    assert "simulated" not in r.text
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Organization)) == 0
    assert (
        db_session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.action == "org.created")
        )
        == 0
    )


def test_service_rollback_leaves_no_partial_rows(app: FastAPI, db_session: Session) -> None:
    ApiUser(app).signup()
    user_id = db_session.scalars(select(User.id)).one()
    session = app.state.session_factory()
    service = OrganizationService(session, AuditService(app.state.session_factory))
    original = OrganizationRepository.add_member

    def failing(self: OrganizationRepository, **kwargs: Any) -> OrganizationMember:
        original(self, **kwargs)
        raise RuntimeError("fail after both inserts flushed")

    OrganizationRepository.add_member = failing  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):
            service.create(user_id=user_id, name="Half", client=ClientInfo(None, None))
        session.rollback()
    finally:
        OrganizationRepository.add_member = original  # type: ignore[method-assign]
        session.close()
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Organization)) == 0
    assert db_session.scalar(select(func.count()).select_from(OrganizationMember)) == 0


def test_service_level_denial_is_audited_without_lock_wait(org: Org, db_session: Session) -> None:
    owner_id = org.owner.me_id()
    r = org.admin.patch(f"{org.base}/members/{owner_id}", {"role": "member"})
    assert r.status_code == 403
    r = org.admin.delete(f"{org.base}/members/{owner_id}")
    assert r.status_code == 403
    ops = sorted(
        str(row.details.get("operation"))
        for row in db_session.scalars(
            select(AuditLog).where(AuditLog.action == "authz.permission_denied")
        )
    )
    assert ops == ["member.change_role", "member.remove"]
