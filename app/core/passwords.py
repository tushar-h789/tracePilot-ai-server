"""Password hashing (Argon2id via argon2-cffi) and password policy.

- Explicit Argon2id parameters from settings; `needs_rehash` lets login upgrade old hashes.
- `verify_dummy` burns the same work when the account does not exist (timing equalisation).
- Policy: length 12..128 characters, no composition rules, small local common-password list
  (NIST SP 800-63B style). No external breach API (zero-budget, no third parties).
"""

from __future__ import annotations

import secrets
from functools import cache
from pathlib import Path

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128  # bounds hashing cost and request size
_COMMON_PASSWORDS_FILE = Path(__file__).parent / "data" / "common_passwords.txt"


@cache
def common_passwords() -> frozenset[str]:
    lines = _COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines()
    return frozenset(
        line.strip().lower() for line in lines if line.strip() and not line.startswith("#")
    )


def password_policy_errors(password: str, email: str | None = None) -> list[str]:
    """Return human-readable policy violations (empty list = acceptable)."""
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters long.")
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Password must be at most {MAX_PASSWORD_LENGTH} characters long.")
    lowered = password.lower()
    if lowered in common_passwords():
        errors.append("This password is too common. Choose a less predictable one.")
    if email and lowered == email.lower():
        errors.append("Password must not be the same as your email address.")
    return errors


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash: str | None = None

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        return self._hasher.check_needs_rehash(password_hash)

    def verify_dummy(self, password: str) -> None:
        """Spend one verification on a throwaway hash so missing users cost the same time."""
        if self._dummy_hash is None:
            self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(24))
        self.verify(self._dummy_hash, password)
