"""Audit trail for security-sensitive actions.

- `record` joins the caller's transaction (the event commits/rolls back with the change).
- `record_independent` commits in its own session: used for failures/denials whose request
  transaction is about to be rolled back (login failure, permission denied, rate limited).
Details are passed through the log redactor as a last line of defence against secrets.
"""

from __future__ import annotations

import logging
import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.logging import redact
from app.repositories.audit import AuditRepository

logger = logging.getLogger("app.audit")


class AuditAction(StrEnum):
    REGISTER = "auth.register"
    REGISTER_DUPLICATE = "auth.register_duplicate"
    LOGIN_SUCCEEDED = "auth.login_succeeded"
    LOGIN_FAILED = "auth.login_failed"
    LOGIN_RATE_LIMITED = "auth.login_rate_limited"
    LOGOUT = "auth.logout"
    LOGOUT_ALL = "auth.logout_all"
    SESSION_REVOKED = "auth.session_revoked"
    ORG_CREATED = "org.created"
    ORG_UPDATED = "org.updated"
    MEMBER_ADDED = "org.member_added"
    MEMBER_ROLE_CHANGED = "org.member_role_changed"
    MEMBER_REMOVED = "org.member_removed"
    PERMISSION_DENIED = "authz.permission_denied"
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_ARCHIVED = "project.archived"
    PROJECT_RESTORED = "project.restored"
    INGESTION_KEY_CREATED = "ingestion_key.created"
    INGESTION_KEY_REVOKED = "ingestion_key.revoked"
    INGESTION_KEY_ROTATED = "ingestion_key.rotated"


class AuditService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def record(
        db: Session,
        action: AuditAction,
        *,
        actor_user_id: uuid.UUID | None = None,
        org_id: uuid.UUID | None = None,
        target_type: str | None = None,
        target_id: object | None = None,
        ip_address: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        AuditRepository(db).add(
            action=action.value,
            actor_user_id=actor_user_id,
            org_id=org_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            ip_address=ip_address,
            details=redact(details or {}),
        )
        logger.info(
            "audit",
            extra={
                "action": action.value,
                "actor_user_id": str(actor_user_id) if actor_user_id else None,
                "org_id": str(org_id) if org_id else None,
                "target_type": target_type,
                "target_id": str(target_id) if target_id is not None else None,
            },
        )

    def record_independent(self, action: AuditAction, **kwargs: Any) -> None:
        try:
            with self._session_factory() as db:
                self.record(db, action, **kwargs)
                db.commit()
        except SQLAlchemyError:
            # Never turn an auth failure into a 500 because the audit write failed.
            logger.exception("audit_write_failed", extra={"action": action.value})
