"""Secrets never reach logs: passwords, session tokens, CSRF tokens, cookies, auth headers."""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI

from app.core.logging import REDACTED, JsonFormatter, redact
from tests.helpers import ApiUser


def test_redact_covers_auth_material() -> None:
    payload = {
        "password": "p",
        "new_password": "p",
        "password_hash": "$argon2id$...",
        "session_token": "s",
        "csrf_token": "c",
        "X-CSRF-Token": "c",
        "cookie": "tp_session=abc",
        "set-cookie": "tp_session=abc",
        "Authorization": "Bearer x",
        "credentials": "x",
        "email_domain": "example.com",
    }
    out = redact(payload)
    assert all(out[k] == REDACTED for k in payload if k != "email_domain")
    assert out["email_domain"] == "example.com"


@pytest.mark.integration
def test_auth_flow_logs_contain_no_secrets(app: FastAPI, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    user = ApiUser(app, password="very-distinct-password-7781")
    user.register()
    user.login(password="wrong-password-for-log-test")
    user.login()
    session_token = user.session_cookie
    csrf_token = user.client.cookies.get("tp_csrf")
    user.create_org("Logs Inc")
    user.post("/auth/logout")
    assert session_token
    assert csrf_token

    formatter = JsonFormatter()
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert "audit" in rendered
    assert "request" in rendered
    for secret in (
        "very-distinct-password-7781",
        "wrong-password-for-log-test",
        session_token,
        csrf_token,
        "$argon2",
        "tp_session=",
    ):
        assert secret not in rendered, f"secret leaked into logs: {secret[:6]}..."
    assert user.email not in rendered  # auth logs reference user ids, not emails
