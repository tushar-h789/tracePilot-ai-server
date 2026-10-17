"""Unit tests (no services): passwords, emails, tokens, CSRF, client IP, RBAC policy."""

from __future__ import annotations

from typing import Any

import pytest
from argon2 import PasswordHasher, Type
from starlette.requests import Request

from app.core.csrf import binding_for, issue_csrf_token, verify_csrf
from app.core.errors import ConflictError, PermissionDeniedError, RateLimitedError
from app.core.identity import generate_token, hash_token, is_valid_email, normalize_email
from app.core.net import client_info, client_ip
from app.core.passwords import (
    MAX_PASSWORD_LENGTH,
    PasswordService,
    common_passwords,
    password_policy_errors,
)
from app.core.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    check_can_add_member,
    check_can_change_role,
    check_can_remove_member,
    role_has,
)
from tests.conftest import SettingsFactory

SECRET = b"unit-test-csrf-secret-0123456789abcdef"


# --- passwords -------------------------------------------------------------------------------
def test_production_defaults_produce_argon2id_with_explicit_params(
    make_settings: SettingsFactory,
) -> None:
    settings = make_settings(argon2_time_cost=3, argon2_memory_cost_kib=65536, argon2_parallelism=4)
    service = PasswordService(settings)
    hashed = service.hash("a perfectly fine passphrase")
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert service.verify(hashed, "a perfectly fine passphrase")
    assert not service.verify(hashed, "wrong passphrase!!")
    assert not service.needs_rehash(hashed)


def test_needs_rehash_when_parameters_change(make_settings: SettingsFactory) -> None:
    old = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1, type=Type.ID)
    service = PasswordService(make_settings(argon2_time_cost=2))
    assert service.needs_rehash(old.hash("whatever-passphrase"))


def test_verify_handles_garbage_hash(make_settings: SettingsFactory) -> None:
    service = PasswordService(make_settings())
    assert service.verify("not-a-hash", "x") is False
    service.verify_dummy("anything")  # must not raise
    service.verify_dummy("anything again")


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short", "at least 12"),
        ("x" * (MAX_PASSWORD_LENGTH + 1), "at most 128"),
        ("Password1234", "too common"),
        ("someone@example.com", "same as your email"),
    ],
)
def test_password_policy_rejections(password: str, fragment: str) -> None:
    errors = password_policy_errors(password, "someone@example.com")
    assert any(fragment in e for e in errors), errors


def test_password_policy_has_no_composition_rules() -> None:
    assert password_policy_errors("all lowercase words here") == []
    assert len(common_passwords()) > 20


# --- emails / tokens -------------------------------------------------------------------------
def test_email_normalisation() -> None:
    assert normalize_email("  Alice.Smith@Example.COM ") == "alice.smith@example.com"
    assert normalize_email("\uff21\uff22\uff23@example.com") == "abc@example.com"  # NFKC


@pytest.mark.parametrize(
    ("email", "ok"),
    [
        ("a@example.com", True),
        ("no-at-sign.example.com", False),
        ("two@@example.com", False),
        ("spaces in@example.com", False),
        ("a@localhost", False),
        (("x" * 250) + "@example.com", False),
    ],
)
def test_email_validation(email: str, ok: bool) -> None:
    assert is_valid_email(email) is ok


def test_tokens_are_random_and_hashed() -> None:
    a, b = generate_token(), generate_token()
    assert a != b
    assert len(a) >= 43  # 32 bytes urlsafe
    assert len(hash_token(a)) == 64
    assert hash_token(a) != a


# --- CSRF ------------------------------------------------------------------------------------
def test_csrf_valid_roundtrip() -> None:
    token = issue_csrf_token(SECRET, "session-token")
    assert verify_csrf(
        SECRET, header_value=token, cookie_value=token, session_token="session-token"
    )


@pytest.mark.parametrize(
    "case",
    ["missing_header", "missing_cookie", "mismatch", "other_session", "tampered", "anon_after"],
)
def test_csrf_rejections(case: str) -> None:
    token = issue_csrf_token(SECRET, "session-token")
    other = issue_csrf_token(SECRET, "session-token")
    kwargs: dict[str, Any] = {
        "header_value": token,
        "cookie_value": token,
        "session_token": "session-token",
    }
    if case == "missing_header":
        kwargs["header_value"] = None
    elif case == "missing_cookie":
        kwargs["cookie_value"] = None
    elif case == "mismatch":
        kwargs["header_value"] = other
    elif case == "other_session":
        kwargs["session_token"] = "another-session"
    elif case == "tampered":
        nonce, _, sig = token.partition(".")
        forged = f"{nonce}x.{sig}"
        kwargs["header_value"] = kwargs["cookie_value"] = forged
    elif case == "anon_after":
        kwargs["session_token"] = None
    assert verify_csrf(SECRET, **kwargs) is False


def test_csrf_anonymous_binding() -> None:
    token = issue_csrf_token(SECRET, None)
    assert binding_for(None) == "anon"
    assert verify_csrf(SECRET, header_value=token, cookie_value=token, session_token=None)
    assert not verify_csrf(
        SECRET, header_value="garbage", cookie_value="garbage", session_token=None
    )


# --- client IP -------------------------------------------------------------------------------
def _request(peer: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "client": (peer, 1234), "headers": raw})


def test_client_ip_ignores_forwarded_for_from_untrusted_peer() -> None:
    req = _request("203.0.113.9", {"X-Forwarded-For": "1.2.3.4"})
    assert client_ip(req, []) == "203.0.113.9"
    assert client_ip(req, ["10.0.0.0/8"]) == "203.0.113.9"


def test_client_ip_uses_last_hop_from_trusted_proxy() -> None:
    req = _request("10.1.2.3", {"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})
    assert client_ip(req, ["10.0.0.0/8"]) == "198.51.100.7"
    bad = _request("10.1.2.3", {"X-Forwarded-For": "not-an-ip"})
    assert client_ip(bad, ["10.0.0.0/8"]) == "10.1.2.3"


def test_client_info_truncates_user_agent() -> None:
    info = client_info(_request("127.0.0.1", {"User-Agent": "x" * 1000}), [])
    assert info.user_agent is not None
    assert len(info.user_agent) == 256


def test_rate_limited_error_sets_retry_after() -> None:
    assert RateLimitedError(0).headers["Retry-After"] == "1"
    assert RateLimitedError(42).headers["Retry-After"] == "42"


# --- RBAC policy -----------------------------------------------------------------------------
def test_matrix_matches_project_plan() -> None:
    assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)
    assert not role_has(Role.ADMIN, Permission.OWNERS_MANAGE)
    assert not role_has(Role.ADMIN, Permission.ORG_DELETE)
    assert role_has(Role.ADMIN, Permission.MEMBERS_MANAGE)
    assert not role_has(Role.MEMBER, Permission.MEMBERS_MANAGE)
    assert role_has(Role.MEMBER, Permission.INCIDENT_ACT)
    assert not role_has(Role.VIEWER, Permission.INCIDENT_ACT)
    assert not role_has(Role.VIEWER, Permission.KEYS_READ_METADATA)
    for role in Role:
        assert role_has(role, Permission.ORG_READ)
        assert role_has(role, Permission.MEMBERS_READ)


@pytest.mark.parametrize(
    ("actor", "new_role", "allowed"),
    [
        (Role.OWNER, Role.OWNER, True),
        (Role.OWNER, Role.ADMIN, True),
        (Role.ADMIN, Role.ADMIN, True),
        (Role.ADMIN, Role.VIEWER, True),
        (Role.ADMIN, Role.OWNER, False),
        (Role.MEMBER, Role.VIEWER, False),
        (Role.VIEWER, Role.VIEWER, False),
    ],
)
def test_add_member_policy(actor: Role, new_role: Role, allowed: bool) -> None:
    if allowed:
        check_can_add_member(actor, new_role)
    else:
        with pytest.raises(PermissionDeniedError):
            check_can_add_member(actor, new_role)


@pytest.mark.parametrize(
    ("actor", "target", "new", "is_self", "owners", "expected"),
    [
        (Role.OWNER, Role.MEMBER, Role.ADMIN, False, 1, None),
        (Role.OWNER, Role.OWNER, Role.ADMIN, False, 2, None),
        (Role.OWNER, Role.OWNER, Role.ADMIN, True, 1, ConflictError),  # last owner
        (Role.OWNER, Role.MEMBER, Role.OWNER, False, 1, None),
        (Role.ADMIN, Role.MEMBER, Role.ADMIN, False, 1, None),
        (Role.ADMIN, Role.ADMIN, Role.MEMBER, False, 1, None),
        (Role.ADMIN, Role.MEMBER, Role.OWNER, False, 1, PermissionDeniedError),
        (Role.ADMIN, Role.OWNER, Role.MEMBER, False, 2, PermissionDeniedError),
        (Role.ADMIN, Role.ADMIN, Role.OWNER, True, 1, PermissionDeniedError),  # self-escalation
        (Role.ADMIN, Role.ADMIN, Role.VIEWER, True, 1, None),  # self-demotion is fine
        (Role.MEMBER, Role.MEMBER, Role.ADMIN, True, 1, PermissionDeniedError),
        (Role.MEMBER, Role.VIEWER, Role.MEMBER, False, 1, PermissionDeniedError),
        (Role.VIEWER, Role.VIEWER, Role.MEMBER, True, 1, PermissionDeniedError),
    ],
)
def test_change_role_policy(
    actor: Role,
    target: Role,
    new: Role,
    is_self: bool,
    owners: int,
    expected: type[Exception] | None,
) -> None:
    kwargs: dict[str, Any] = {
        "actor_role": actor,
        "target_role": target,
        "new_role": new,
        "is_self": is_self,
        "owner_count": owners,
    }
    if expected is None:
        check_can_change_role(**kwargs)
    else:
        with pytest.raises(expected):
            check_can_change_role(**kwargs)


@pytest.mark.parametrize(
    ("actor", "target", "owners", "expected"),
    [
        (Role.OWNER, Role.MEMBER, 1, None),
        (Role.OWNER, Role.OWNER, 2, None),
        (Role.OWNER, Role.OWNER, 1, ConflictError),
        (Role.ADMIN, Role.MEMBER, 1, None),
        (Role.ADMIN, Role.ADMIN, 1, None),
        (Role.ADMIN, Role.OWNER, 2, PermissionDeniedError),
        (Role.MEMBER, Role.VIEWER, 1, PermissionDeniedError),
        (Role.VIEWER, Role.MEMBER, 1, PermissionDeniedError),
    ],
)
def test_remove_member_policy(
    actor: Role, target: Role, owners: int, expected: type[Exception] | None
) -> None:
    if expected is None:
        check_can_remove_member(actor_role=actor, target_role=target, owner_count=owners)
    else:
        with pytest.raises(expected):
            check_can_remove_member(actor_role=actor, target_role=target, owner_count=owners)
