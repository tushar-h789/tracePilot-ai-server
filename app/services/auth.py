"""Registration, login, sessions (ADR-018).

Opaque 256-bit session tokens live only in an HttpOnly cookie; the DB stores SHA-256(token).
Every protected request re-checks revocation, absolute expiry and idle timeout.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import (
    InvalidCredentialsError,
    NotAuthenticatedError,
    NotFoundError,
    RateLimitedError,
    ValidationFailedError,
)
from app.core.identity import generate_token, hash_token, is_valid_email, normalize_email
from app.core.net import ClientInfo
from app.core.passwords import PasswordService, password_policy_errors
from app.core.rate_limit import FixedWindowRateLimiter, rate_limit_key
from app.models.session import UserSession
from app.models.user import User
from app.repositories.sessions import SessionRepository
from app.repositories.users import UserRepository
from app.services.audit import AuditAction, AuditService

logger = logging.getLogger("app.auth")


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: UserSession
    token: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    user: User
    session: UserSession
    token: str


def _now() -> datetime:
    return datetime.now(UTC)


class AuthService:
    def __init__(
        self,
        db: Session,
        settings: Settings,
        passwords: PasswordService,
        limiter: FixedWindowRateLimiter,
        audit: AuditService,
    ) -> None:
        self.db = db
        self.settings = settings
        self.passwords = passwords
        self.limiter = limiter
        self.audit = audit
        self.users = UserRepository(db)
        self.sessions = SessionRepository(db)

    # --- registration -----------------------------------------------------------------------
    def register(
        self, *, email: str, password: str, display_name: str | None, client: ClientInfo
    ) -> None:
        """Create an account. Duplicate emails get the same response and no second account."""
        if client.ip:
            result = self.limiter.hit(
                rate_limit_key("register:ip", client.ip),
                limit=self.settings.register_rate_limit_per_ip,
                window_seconds=self.settings.register_rate_limit_window_seconds,
            )
            if not result.allowed:
                raise RateLimitedError(result.retry_after)

        normalized = normalize_email(email)
        if not is_valid_email(normalized):
            raise ValidationFailedError(
                "Enter a valid email address.",
                errors=[{"loc": ["body", "email"], "msg": "Enter a valid email address."}],
            )
        problems = password_policy_errors(password, normalized)
        if problems:
            raise ValidationFailedError(
                "Password does not meet the requirements.",
                errors=[{"loc": ["body", "password"], "msg": msg} for msg in problems],
            )

        name = (display_name or "").strip() or normalized.split("@", 1)[0][:100]
        existing = self.users.get_by_email(normalized)
        password_hash = self.passwords.hash(password)  # always hash: equalises timing
        if existing is not None:
            self._audit_duplicate(existing.id, client)
            return
        try:
            user = self.users.add(email=normalized, password_hash=password_hash, display_name=name)
            self.audit.record(
                self.db,
                AuditAction.REGISTER,
                actor_user_id=user.id,
                target_type="user",
                target_id=user.id,
                ip_address=client.ip,
            )
            self.db.commit()
        except IntegrityError:
            # Concurrent registration of the same email: the unique index wins, same response.
            self.db.rollback()
            raced = self.users.get_by_email(normalized)
            self._audit_duplicate(raced.id if raced else None, client)

    def _audit_duplicate(self, user_id: uuid.UUID | None, client: ClientInfo) -> None:
        self.audit.record_independent(
            AuditAction.REGISTER_DUPLICATE,
            actor_user_id=None,
            target_type="user",
            target_id=user_id,
            ip_address=client.ip,
        )

    # --- login ------------------------------------------------------------------------------
    def _enforce_login_rate_limit(self, normalized_email: str, client: ClientInfo) -> None:
        window = self.settings.login_rate_limit_window_seconds
        checks = [("email", normalized_email, self.settings.login_rate_limit_per_email)]
        if client.ip:
            checks.append(("ip", client.ip, self.settings.login_rate_limit_per_ip))
        blocked: list[tuple[str, int]] = []
        for scope, identifier, limit in checks:
            result = self.limiter.hit(
                rate_limit_key(f"login:{scope}", identifier), limit=limit, window_seconds=window
            )
            if not result.allowed:
                blocked.append((scope, result.retry_after))
        if blocked:
            self.audit.record_independent(
                AuditAction.LOGIN_RATE_LIMITED,
                ip_address=client.ip,
                details={"scopes": [scope for scope, _ in blocked]},
            )
            raise RateLimitedError(max(retry for _, retry in blocked))

    def login(
        self, *, email: str, password: str, client: ClientInfo, current_token: str | None
    ) -> LoginResult:
        normalized = normalize_email(email)
        self._enforce_login_rate_limit(normalized, client)

        user = self.users.get_by_email(normalized) if is_valid_email(normalized) else None
        if user is None:
            self.passwords.verify_dummy(password)
            self._login_failed(None, "unknown_account", client)
        if not self.passwords.verify(user.password_hash, password):
            self._login_failed(user.id, "bad_password", client)
        if not user.is_active:
            self._login_failed(user.id, "inactive", client)

        now = _now()
        if self.passwords.needs_rehash(user.password_hash):
            self.users.set_password_hash(user, self.passwords.hash(password))
        if current_token:
            # Session fixation defence: never reuse a pre-login session; revoke any presented one.
            previous = self.sessions.get_by_token_hash(hash_token(current_token))
            if previous is not None:
                self.sessions.revoke(previous, now, "replaced")

        token = generate_token()
        session = self.sessions.add(
            user_id=user.id,
            token_hash=hash_token(token),
            now=now,
            expires_at=now + timedelta(hours=self.settings.session_absolute_timeout_hours),
            ip_address=client.ip,
            user_agent=client.user_agent,
        )
        self.users.mark_login(user, now)
        self.audit.record(
            self.db,
            AuditAction.LOGIN_SUCCEEDED,
            actor_user_id=user.id,
            target_type="session",
            target_id=session.id,
            ip_address=client.ip,
        )
        self.db.commit()
        return LoginResult(user=user, session=session, token=token)

    def _login_failed(self, user_id: uuid.UUID | None, reason: str, client: ClientInfo) -> NoReturn:
        # `reason` is for the audit trail only; the client always sees the same message.
        self.audit.record_independent(
            AuditAction.LOGIN_FAILED,
            actor_user_id=user_id,
            target_type="user" if user_id else None,
            target_id=user_id,
            ip_address=client.ip,
            details={"reason": reason},
        )
        raise InvalidCredentialsError()

    # --- session validation -----------------------------------------------------------------
    def authenticate(self, token: str | None) -> AuthContext:
        if not token or len(token) > 128:
            raise NotAuthenticatedError()
        session = self.sessions.get_by_token_hash(hash_token(token))
        now = _now()
        if session is None or not self._is_usable(session, now):
            raise NotAuthenticatedError()
        touch_after = timedelta(seconds=self.settings.session_touch_interval_seconds)
        if now - session.last_seen_at >= touch_after:
            session.last_seen_at = now
            self.db.commit()
        return AuthContext(user=session.user, session=session, token=token)

    def _is_usable(self, session: UserSession, now: datetime) -> bool:
        idle = timedelta(minutes=self.settings.session_idle_timeout_minutes)
        return (
            session.revoked_at is None
            and session.expires_at > now
            and session.last_seen_at + idle > now
            and session.user.is_active
        )

    # --- logout / revocation ----------------------------------------------------------------
    def logout(self, token: str | None, client: ClientInfo) -> None:
        """Idempotent: revokes the presented session if it exists and is still active."""
        if not token or len(token) > 128:
            return
        session = self.sessions.get_by_token_hash(hash_token(token))
        if session is None or session.revoked_at is not None:
            return
        self.sessions.revoke(session, _now(), "logout")
        self.audit.record(
            self.db,
            AuditAction.LOGOUT,
            actor_user_id=session.user_id,
            target_type="session",
            target_id=session.id,
            ip_address=client.ip,
        )
        self.db.commit()

    def logout_all(self, ctx: AuthContext, client: ClientInfo) -> int:
        count = self.sessions.revoke_all_for_user(ctx.user.id, _now(), "logout_all")
        self.audit.record(
            self.db,
            AuditAction.LOGOUT_ALL,
            actor_user_id=ctx.user.id,
            target_type="user",
            target_id=ctx.user.id,
            ip_address=client.ip,
            details={"revoked_sessions": count},
        )
        self.db.commit()
        return count

    def list_sessions(self, ctx: AuthContext) -> list[UserSession]:
        return self.sessions.list_active_for_user(
            ctx.user.id, _now(), self.settings.max_sessions_listed
        )

    def revoke_session(self, ctx: AuthContext, session_id: uuid.UUID, client: ClientInfo) -> bool:
        """Revoke one of the caller's sessions. Returns True when it was the current one."""
        target = self.sessions.get_for_user(session_id, ctx.user.id)
        if target is None or target.revoked_at is not None:
            raise NotFoundError("Session not found.")
        self.sessions.revoke(target, _now(), "revoked_by_user")
        self.audit.record(
            self.db,
            AuditAction.SESSION_REVOKED,
            actor_user_id=ctx.user.id,
            target_type="session",
            target_id=target.id,
            ip_address=client.ip,
        )
        self.db.commit()
        return target.id == ctx.session.id
