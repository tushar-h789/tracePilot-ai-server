"""Email normalisation/validation and opaque token helpers."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata

MAX_EMAIL_LENGTH = 254
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


def normalize_email(raw: str) -> str:
    """Canonical form used for storage, uniqueness and rate-limit keys.

    NFKC + trim + lower-case of the whole address. Lower-casing the local part is technically
    lossy (RFC 5321 allows case-sensitive local parts) but no mainstream provider relies on it,
    and it prevents duplicate accounts that differ only by case.
    """
    return unicodedata.normalize("NFKC", raw).strip().lower()


def is_valid_email(email: str) -> bool:
    return len(email) <= MAX_EMAIL_LENGTH and _EMAIL_RE.fullmatch(email) is not None


def generate_token(nbytes: int = 32) -> str:
    """High-entropy opaque token (256 bits by default), URL-safe."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest. A fast hash is fine: tokens are random, not user-chosen."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
