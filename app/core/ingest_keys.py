"""Ingestion key format, hashing and verification (ADR-021).

Format: ``tp_<public id>_<secret>``
- ``public id``: 12 lowercase hex chars (48 bits, random). ``tp_<public id>`` is the stored,
  displayable *prefix* used for lookup; it is not secret.
- ``secret``: ``secrets.token_urlsafe(32)`` -> 43 URL-safe chars, 256 bits of entropy.

Storage: ``HMAC-SHA256(INGEST_KEY_PEPPER, full key)`` as 64 hex chars. A database leak alone
does not let anyone recompute or verify keys without the pepper; comparison is constant-time.
A slow KDF is unnecessary because the secret has 256 bits of entropy.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

KEY_SCHEME = "tp_"
PUBLIC_ID_HEX_CHARS = 12
SECRET_BYTES = 32
_KEY_RE = re.compile(r"^tp_([0-9a-f]{12})_([A-Za-z0-9_-]{43})$")
_MAX_KEY_CHARS = 80


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    plaintext: str
    prefix: str

    def __repr__(self) -> str:  # never print the secret
        return f"GeneratedKey(prefix={self.prefix!r}, plaintext='[REDACTED]')"


def generate_key() -> GeneratedKey:
    public_id = secrets.token_hex(PUBLIC_ID_HEX_CHARS // 2)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return GeneratedKey(plaintext=f"{KEY_SCHEME}{public_id}_{secret}", prefix=f"tp_{public_id}")


def parse_prefix(key: str) -> str | None:
    """Return the public prefix if `key` is well-formed, else None (no DB lookup needed)."""
    if len(key) > _MAX_KEY_CHARS:
        return None
    match = _KEY_RE.match(key)
    return f"tp_{match.group(1)}" if match else None


def hash_key(pepper: bytes, key: str) -> str:
    return hmac.new(pepper, key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_key(pepper: bytes, key: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_key(pepper, key), stored_hash)


def bearer_token(authorization: str | None) -> str | None:
    """Extract the token from ``Authorization: Bearer <token>`` (scheme case-insensitive)."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None
