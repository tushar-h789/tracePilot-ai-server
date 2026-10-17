"""Authentication + session integration tests (real Postgres + Redis)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import AuditLog, User, UserSession
from tests.helpers import API, ApiUser, unique_email

pytestmark = pytest.mark.integration

GENERIC_LOGIN_ERROR = "Invalid email or password."


def _set_cookie_headers(response: Any) -> list[str]:
    return [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]


def _actions(db: Session) -> list[str]:
    db.expire_all()
    return list(db.scalars(select(AuditLog.action).order_by(AuditLog.created_at)))


# --- registration / login ------------------------------------------------------------------
def test_register_login_me_logout_flow(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app, email="  Alice@Example.com ")
    response = user.register(display_name="Alice")
    assert response.status_code == 202
    assert "Set-Cookie" not in response.headers  # registration does not log in

    response = user.login()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["display_name"] == "Alice"
    assert "password" not in response.text
    assert "$argon2" not in response.text

    cookies = _set_cookie_headers(response)
    session_cookie = next(c for c in cookies if c.startswith("tp_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("tp_csrf="))
    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    assert "Path=/" in session_cookie
    assert "HttpOnly" not in csrf_cookie  # readable for double-submit
    assert "Domain" not in session_cookie

    me = user.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "alice@example.com"

    assert user.post("/auth/logout").status_code == 204
    assert user.get("/auth/me").status_code == 401
    assert _actions(db_session) == [
        "auth.register",
        "auth.login_succeeded",
        "auth.logout",
    ]


def test_password_hash_is_argon2id_and_never_returned(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    stored = db_session.scalars(select(User.password_hash)).one()
    assert stored.startswith("$argon2id$")
    for path in ("/auth/me", "/auth/sessions"):
        assert "$argon2" not in user.get(path).text


def test_duplicate_registration_is_generic_and_creates_no_account(
    app: FastAPI, db_session: Session
) -> None:
    first = ApiUser(app, email="dup@example.com")
    r1 = first.register()
    second = ApiUser(app, email="DUP@example.com", password="another-long-password-2")
    r2 = second.register()
    assert r1.status_code == r2.status_code == 202
    assert r1.json() == r2.json()
    assert db_session.scalar(select(func.count()).select_from(User)) == 1
    # The second password does not work; the original owner's account is untouched.
    assert second.login().status_code == 401
    assert first.login().status_code == 200
    assert "auth.register_duplicate" in _actions(db_session)


def test_registration_policy_errors_do_not_echo_password(app: FastAPI) -> None:
    user = ApiUser(app, password="short-pw")
    response = user.register()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "short-pw" not in response.text
    assert "at least 12" in response.text
    common = ApiUser(app, password="password1234").register()
    assert common.status_code == 422
    assert "too common" in common.text
    bad_email = ApiUser(app, email="not-an-email").register()
    assert bad_email.status_code == 422


def test_schema_validation_errors_do_not_echo_input(app: FastAPI) -> None:
    user = ApiUser(app)
    response = user.post(
        "/auth/register", {"email": "a@example.com", "password": "s3cret-value", "role": "owner"}
    )
    assert response.status_code == 422
    assert "s3cret-value" not in response.text
    assert "extra_forbidden" in response.text


def test_invalid_credentials_are_generic(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    wrong_password = ApiUser(app, email=user.email).login(password="wrong-password-123")
    unknown = ApiUser(app, email=unknown_email()).login()
    malformed = ApiUser(app, email="no-at-sign").login()
    for response in (wrong_password, unknown, malformed):
        assert response.status_code == 401
        assert response.json()["detail"] == GENERIC_LOGIN_ERROR
        assert response.json()["code"] == "invalid_credentials"
    assert wrong_password.json().keys() == unknown.json().keys()
    failed = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "auth.login_failed")
    ).all()
    reasons = sorted(row.details["reason"] for row in failed)
    assert reasons == ["bad_password", "unknown_account", "unknown_account"]


def unknown_email() -> str:
    return unique_email("ghost")


def test_inactive_user_cannot_login(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app)
    assert user.register().status_code == 202
    db_session.execute(update(User).values(is_active=False))
    db_session.commit()
    response = user.login()
    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_LOGIN_ERROR


def test_login_rehashes_when_parameters_change(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app)
    assert user.register().status_code == 202
    weak = PasswordHasher(time_cost=1, memory_cost=512, parallelism=1, type=Type.ID)
    db_session.execute(update(User).values(password_hash=weak.hash(user.password)))
    db_session.commit()
    assert user.login().status_code == 200
    db_session.expire_all()
    new_hash = db_session.scalars(select(User.password_hash)).one()
    assert new_hash.startswith("$argon2id$v=19$m=1024,t=1,p=1$")  # app params (test config)


# --- sessions --------------------------------------------------------------------------------
def test_session_token_is_stored_hashed(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    token = user.session_cookie
    assert token
    row = db_session.scalars(select(UserSession)).one()
    assert row.token_hash != token
    assert len(row.token_hash) == 64


def test_absolute_expiry_rejects_session(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    past = datetime.now(UTC) - timedelta(seconds=1)
    db_session.execute(
        update(UserSession).values(created_at=past - timedelta(hours=1), expires_at=past)
    )
    db_session.commit()
    response = user.get("/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_idle_timeout_rejects_session(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    stale = datetime.now(UTC) - timedelta(minutes=61)
    db_session.execute(update(UserSession).values(last_seen_at=stale))
    db_session.commit()
    assert user.get("/auth/me").status_code == 401


def test_last_seen_is_touched_after_interval(make_app: Any, db_session: Session) -> None:
    app = make_app(session_touch_interval_seconds=0)
    user = ApiUser(app).signup()
    before = db_session.scalars(select(UserSession.last_seen_at)).one()
    assert user.get("/auth/me").status_code == 200
    db_session.expire_all()
    assert db_session.scalars(select(UserSession.last_seen_at)).one() > before


def test_revoked_session_is_rejected(app: FastAPI) -> None:
    laptop = ApiUser(app).signup()
    phone = ApiUser(app, email=laptop.email)
    assert phone.login().status_code == 200

    sessions = laptop.get("/auth/sessions").json()["items"]
    assert len(sessions) == 2
    assert sum(1 for s in sessions if s["current"]) == 1
    phone_session = next(s for s in sessions if not s["current"])

    assert laptop.delete(f"/auth/sessions/{phone_session['id']}").status_code == 204
    assert phone.get("/auth/me").status_code == 401
    assert laptop.get("/auth/me").status_code == 200
    # Cannot revoke it twice, nor somebody else's session.
    assert laptop.delete(f"/auth/sessions/{phone_session['id']}").status_code == 404


def test_cannot_revoke_another_users_session(app: FastAPI) -> None:
    alice = ApiUser(app).signup()
    bob = ApiUser(app).signup()
    bob_session = bob.get("/auth/sessions").json()["items"][0]["id"]
    assert alice.delete(f"/auth/sessions/{bob_session}").status_code == 404
    assert bob.get("/auth/me").status_code == 200


def test_revoking_current_session_clears_cookie(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    current = user.get("/auth/sessions").json()["items"][0]["id"]
    response = user.delete(f"/auth/sessions/{current}")
    assert response.status_code == 204
    assert user.session_cookie is None
    assert user.get("/auth/me").status_code == 401


def test_logout_invalidates_token_server_side(app: FastAPI, db_session: Session) -> None:
    user = ApiUser(app).signup()
    stolen = user.session_cookie
    assert stolen
    assert user.post("/auth/logout").status_code == 204
    attacker = TestClient(app, base_url="http://localhost")
    attacker.cookies.set("tp_session", stolen)
    assert attacker.get(f"{API}/auth/me").status_code == 401
    row = db_session.scalars(select(UserSession)).one()
    assert row.revoked_at is not None
    assert row.revoked_reason == "logout"
    # Logout is idempotent.
    assert user.post("/auth/logout").status_code == 204


def test_logout_all_revokes_every_session(app: FastAPI) -> None:
    a = ApiUser(app).signup()
    b = ApiUser(app, email=a.email)
    assert b.login().status_code == 200
    response = a.post("/auth/logout-all")
    assert response.status_code == 200
    assert response.json()["revoked_sessions"] == 2
    assert a.get("/auth/me").status_code == 401
    assert b.get("/auth/me").status_code == 401


def test_login_replaces_presented_session(app: FastAPI, db_session: Session) -> None:
    """Session fixation: a pre-existing session cookie is revoked and a new token issued."""
    user = ApiUser(app).signup()
    old = user.session_cookie
    assert user.login().status_code == 200
    assert user.session_cookie != old
    reasons = db_session.scalars(select(UserSession.revoked_reason)).all()
    assert "replaced" in reasons


def test_unknown_or_oversized_session_cookie_is_401(app: FastAPI) -> None:
    client = TestClient(app, base_url="http://localhost")
    client.cookies.set("tp_session", "x" * 500)
    assert client.get(f"{API}/auth/me").status_code == 401
    client.cookies.set("tp_session", "unknown-token")
    assert client.get(f"{API}/auth/me").status_code == 401


# --- CSRF ------------------------------------------------------------------------------------
def test_csrf_missing_header_is_rejected(app: FastAPI) -> None:
    user = ApiUser(app)
    user.csrf()  # cookie present, header missing
    response = user.post(
        "/auth/register", {"email": user.email, "password": user.password}, csrf=False
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_csrf_mismatch_is_rejected(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    response = user.post("/organizations", {"name": "X"}, headers={"X-CSRF-Token": "a.b"})
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_failed"


def test_csrf_missing_cookie_is_rejected(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    token = user.csrf()
    user.client.cookies.delete("tp_csrf")
    response = user.post("/organizations", {"name": "X"}, headers={"X-CSRF-Token": token})
    assert response.status_code == 403


def test_pre_login_csrf_token_is_not_valid_after_login(app: FastAPI) -> None:
    user = ApiUser(app)
    anon_token = user.csrf()
    assert user.register().status_code == 202
    assert user.login().status_code == 200
    # Force the old (anonymous-bound) token into both cookie and header.
    user.client.cookies.delete("tp_csrf")
    user.client.cookies.set("tp_csrf", anon_token)
    response = user.post("/organizations", {"name": "X"}, headers={"X-CSRF-Token": anon_token})
    assert response.status_code == 403
    # A freshly issued token works.
    user.client.cookies.delete("tp_csrf")
    fresh = user.get("/auth/csrf").json()["csrf_token"]
    response = user.post("/organizations", {"name": "X"}, headers={"X-CSRF-Token": fresh})
    assert response.status_code == 201


def test_foreign_origin_is_rejected(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    response = user.post(
        "/organizations", {"name": "X"}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"
    allowed = user.post(
        "/organizations", {"name": "X"}, headers={"Origin": "http://localhost:3100"}
    )
    assert allowed.status_code == 201


def test_safe_methods_need_no_csrf(app: FastAPI) -> None:
    user = ApiUser(app).signup()
    user.client.cookies.delete("tp_csrf")
    assert user.get("/auth/me").status_code == 200
    assert user.get("/organizations").status_code == 200


# --- rate limiting ---------------------------------------------------------------------------
def test_login_rate_limit_per_email(make_app: Any, db_session: Session) -> None:
    app = make_app(login_rate_limit_per_email=3, login_rate_limit_window_seconds=60)
    user = ApiUser(app).signup()  # 1 attempt
    attempts = [ApiUser(app, email=user.email).login(password="wrong-password-1") for _ in range(2)]
    assert [r.status_code for r in attempts] == [401, 401]
    blocked = ApiUser(app, email=user.email.upper()).login()  # normalised -> same key
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
    assert blocked.json()["code"] == "rate_limited"
    # Correct password is still refused while limited (no oracle).
    assert ApiUser(app, email=user.email).login().status_code == 429
    assert "auth.login_rate_limited" in _actions(db_session)


def test_login_rate_limit_per_ip(make_app: Any) -> None:
    app = make_app(login_rate_limit_per_ip=3, login_rate_limit_per_email=100)
    codes = [ApiUser(app).login().status_code for _ in range(4)]
    assert codes == [401, 401, 401, 429]


def test_register_rate_limit_per_ip(make_app: Any) -> None:
    app = make_app(register_rate_limit_per_ip=2)
    codes = [ApiUser(app).register().status_code for _ in range(3)]
    assert codes == [202, 202, 429]


def test_redis_down_fails_closed(make_app: Any) -> None:
    app = make_app(redis_url="redis://127.0.0.1:1/0", health_check_timeout_seconds=0.2)
    response = ApiUser(app).login()
    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
