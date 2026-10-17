"""Ingestion keys: show-once secret, hash-only storage, revoke, rotate, limits, audit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.ingest_keys import hash_key, parse_prefix
from app.models import AuditLog, IngestionKey
from tests.conftest import TEST_INGEST_PEPPER
from tests.helpers import ApiUser, ingest, make_event

pytestmark = pytest.mark.integration


@pytest.fixture
def owner(app: FastAPI) -> ApiUser:
    return ApiUser(app).signup()


@pytest.fixture
def project(owner: ApiUser) -> dict[str, object]:
    org = owner.create_org("Keys Org")
    return owner.create_project(org["id"], "Keys Project")


def _keys_path(project: dict[str, object]) -> str:
    return f"/projects/{project['id']}/ingestion-keys"


def test_secret_is_shown_once_and_only_hash_is_stored(
    owner: ApiUser, project: dict[str, object], db_session: Session
) -> None:
    response = owner.post(_keys_path(project), {"name": "production"})
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    secret = created["secret"]
    assert parse_prefix(secret) == created["key"]["prefix"]
    assert len(secret) >= 43 + len("tp_") + 13  # 256-bit secret + public id
    assert created["key"]["status"] == "active"

    listed = owner.get(_keys_path(project))
    assert listed.status_code == 200
    assert secret not in listed.text
    assert "secret" not in listed.json()["items"][0]
    assert "key_hash" not in listed.text

    row = db_session.scalars(select(IngestionKey)).one()
    assert row.key_hash == hash_key(TEST_INGEST_PEPPER.encode(), secret)
    assert row.key_hash != secret
    # The plaintext appears nowhere in the database (keys, audit metadata, anything).
    dump = db_session.execute(
        text("SELECT string_agg(t::text, ' ') FROM (SELECT * FROM ingestion_keys) t")
    ).scalar_one()
    audit_dump = db_session.execute(
        text("SELECT string_agg(metadata::text, ' ') FROM audit_logs")
    ).scalar_one()
    secret_part = secret.split("_", 2)[2]
    assert secret_part not in dump
    assert secret_part not in (audit_dump or "")
    assert "secret" not in repr(row)
    assert row.key_hash not in repr(row)


@pytest.mark.parametrize(
    ("role", "list_status", "manage_status"),
    [("owner", 200, 201), ("admin", 200, 201), ("member", 200, 403), ("viewer", 403, 403)],
)
def test_key_permission_matrix(
    app: FastAPI,
    owner: ApiUser,
    project: dict[str, object],
    role: str,
    list_status: int,
    manage_status: int,
) -> None:
    if role == "owner":
        user = owner
    else:
        user = ApiUser(app).signup()
        r = owner.post(
            f"/organizations/{project['org_id']}/members", {"email": user.email, "role": role}
        )
        assert r.status_code == 201
    listed = user.get(_keys_path(project))
    # Viewers lack keys:read_metadata -> 403 (they are members, so not 404).
    assert listed.status_code == list_status
    assert user.post(_keys_path(project), {"name": "k"}).status_code == manage_status
    key_id = owner.create_key(str(project["id"]))["key"]["id"]
    expected = 200 if manage_status == 201 else 403
    assert user.post(f"{_keys_path(project)}/{key_id}/revoke").status_code == expected


def test_revoked_key_is_rejected(app: FastAPI, owner: ApiUser, project: dict[str, object]) -> None:
    created = owner.create_key(str(project["id"]))
    secret = created["secret"]
    assert ingest(app, secret, {"events": [make_event()]}).status_code == 202
    revoked = owner.post(f"{_keys_path(project)}/{created['key']['id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    rejected = ingest(app, secret, {"events": [make_event()]})
    assert rejected.status_code == 401
    assert rejected.json()["code"] == "invalid_ingestion_key"
    assert rejected.headers["www-authenticate"] == "Bearer"
    # Idempotent revoke; revoked keys hidden by default, visible with include_revoked.
    assert owner.post(f"{_keys_path(project)}/{created['key']['id']}/revoke").status_code == 200
    assert owner.get(_keys_path(project)).json()["items"] == []
    assert len(owner.get(_keys_path(project), {"include_revoked": "true"}).json()["items"]) == 1


def test_rotate_without_grace_revokes_old_key(
    app: FastAPI, owner: ApiUser, project: dict[str, object]
) -> None:
    old = owner.create_key(str(project["id"]), "svc")
    r = owner.post(f"{_keys_path(project)}/{old['key']['id']}/rotate", {})
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["key"]["name"] == "svc"
    assert body["previous_key"]["status"] == "revoked"
    assert body["previous_key"]["replaced_by_id"] == body["key"]["id"]
    assert ingest(app, old["secret"], {"events": [make_event()]}).status_code == 401
    assert ingest(app, body["secret"], {"events": [make_event()]}).status_code == 202
    # A revoked key cannot be rotated again.
    again = owner.post(f"{_keys_path(project)}/{old['key']['id']}/rotate", {})
    assert again.status_code == 409


def test_rotate_with_grace_keeps_old_key_until_expiry(
    app: FastAPI, owner: ApiUser, project: dict[str, object], db_session: Session
) -> None:
    old = owner.create_key(str(project["id"]))
    r = owner.post(f"{_keys_path(project)}/{old['key']['id']}/rotate", {"grace_seconds": 600})
    assert r.status_code == 201
    assert r.json()["previous_key"]["status"] == "active"
    assert r.json()["previous_key"]["expires_at"] is not None
    assert ingest(app, old["secret"], {"events": [make_event()]}).status_code == 202
    # Simulate the grace period ending.
    row = db_session.get(IngestionKey, old["key"]["id"])
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    assert ingest(app, old["secret"], {"events": [make_event()]}).status_code == 401
    too_long = owner.post(
        f"{_keys_path(project)}/{r.json()['key']['id']}/rotate", {"grace_seconds": 86_401}
    )
    assert too_long.status_code == 422


def test_active_key_limit(make_app: object, db_session: Session) -> None:
    app = make_app(max_active_keys_per_project=2)  # type: ignore[operator]
    owner = ApiUser(app).signup()
    org = owner.create_org("Limit Org")
    pid = owner.create_project(org["id"])["id"]
    first = owner.create_key(pid, "a")
    owner.create_key(pid, "b")
    third = owner.post(f"/projects/{pid}/ingestion-keys", {"name": "c"})
    assert third.status_code == 409
    owner.post(f"/projects/{pid}/ingestion-keys/{first['key']['id']}/revoke")
    assert owner.post(f"/projects/{pid}/ingestion-keys", {"name": "c"}).status_code == 201


def test_key_ids_from_another_project_are_404(owner: ApiUser, project: dict[str, object]) -> None:
    other = owner.create_project(str(project["org_id"]), "Other Project")
    key = owner.create_key(str(other["id"]))
    r = owner.post(f"{_keys_path(project)}/{key['key']['id']}/revoke")
    assert r.status_code == 404
    r = owner.post(f"{_keys_path(project)}/{key['key']['id']}/rotate", {})
    assert r.status_code == 404


def test_key_lifecycle_is_audited_without_secrets(
    owner: ApiUser, project: dict[str, object], db_session: Session
) -> None:
    created = owner.create_key(str(project["id"]))
    rotated = owner.post(f"{_keys_path(project)}/{created['key']['id']}/rotate", {}).json()
    owner.post(f"{_keys_path(project)}/{rotated['key']['id']}/revoke")
    rows = list(
        db_session.scalars(
            select(AuditLog)
            .where(AuditLog.action.like("ingestion_key.%"))
            .order_by(AuditLog.created_at)
        )
    )
    assert [r.action for r in rows] == [
        "ingestion_key.created",
        "ingestion_key.rotated",
        "ingestion_key.revoked",
    ]
    for row in rows:
        blob = str(row.details)
        assert created["secret"] not in blob
        assert rotated["secret"] not in blob
    assert rows[1].details["new_prefix"] == rotated["key"]["prefix"]


def test_session_cookie_never_authorizes_keys_and_keys_never_authorize_user_api(
    app: FastAPI, owner: ApiUser, project: dict[str, object]
) -> None:
    secret = owner.create_key(str(project["id"]))["secret"]
    # An ingestion key is not a user credential.
    from fastapi.testclient import TestClient

    client = TestClient(app, base_url="http://localhost", raise_server_exceptions=False)
    r = client.get(
        f"/api/v1/projects/{project['id']}", headers={"Authorization": f"Bearer {secret}"}
    )
    assert r.status_code == 401
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code == 401
