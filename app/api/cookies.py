"""Cookie helpers. Session cookie: HttpOnly; CSRF cookie: readable by JS (double-submit).

Both: SameSite=Lax, Path=/, no Domain (host-only), Secure + `__Host-` prefix when
`COOKIE_SECURE=true` (required in prod). Max-Age = absolute session lifetime.
"""

from __future__ import annotations

from fastapi import Response

from app.core.config import Settings
from app.core.csrf import issue_csrf_token


def _secret(settings: Settings) -> bytes:
    return settings.secret_key.get_secret_value().encode()


def set_csrf_cookie(response: Response, settings: Settings, session_token: str | None) -> str:
    token = issue_csrf_token(_secret(settings), session_token)
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.session_absolute_timeout_hours * 3600,
        path="/",
        secure=settings.cookie_secure,
        httponly=False,
        samesite="lax",
    )
    return token


def set_session_cookies(response: Response, settings: Settings, session_token: str) -> str:
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_absolute_timeout_hours * 3600,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return set_csrf_cookie(response, settings, session_token)


def clear_session_cookies(response: Response, settings: Settings) -> str:
    """Delete the session cookie and issue a fresh anonymous CSRF token."""
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return set_csrf_cookie(response, settings, None)
