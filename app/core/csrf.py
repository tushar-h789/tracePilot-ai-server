"""Signed double-submit CSRF tokens (OWASP "signed double-submit cookie").

token = "<nonce>.<hmac>" where hmac = HMAC-SHA256(secret_key, "csrf|<binding>|<nonce>").
`binding` is the SHA-256 of the session cookie value (or "anon" before login), so a token
planted by a sibling sub-domain or minted for another session does not validate.

A request passes when: the `X-CSRF-Token` header equals the CSRF cookie (constant-time) AND the
signature is valid for the session cookie presented with the request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

ANON_BINDING = "anon"
HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def binding_for(session_token: str | None) -> str:
    if not session_token:
        return ANON_BINDING
    return hashlib.sha256(session_token.encode("utf-8")).hexdigest()


def _sign(secret: bytes, binding: str, nonce: str) -> str:
    return hmac.new(secret, f"csrf|{binding}|{nonce}".encode(), hashlib.sha256).hexdigest()


def issue_csrf_token(secret: bytes, session_token: str | None) -> str:
    nonce = secrets.token_urlsafe(32)
    return f"{nonce}.{_sign(secret, binding_for(session_token), nonce)}"


def verify_csrf(
    secret: bytes,
    *,
    header_value: str | None,
    cookie_value: str | None,
    session_token: str | None,
) -> bool:
    if not header_value or not cookie_value or len(header_value) > 256:
        return False
    if not hmac.compare_digest(header_value.encode(), cookie_value.encode()):
        return False
    nonce, sep, signature = header_value.partition(".")
    if not sep or not nonce or not signature:
        return False
    expected = _sign(secret, binding_for(session_token), nonce)
    return hmac.compare_digest(signature.encode(), expected.encode())
