"""Audit-log persistence (append + scoped read)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


class AuditRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self,
        *,
        action: str,
        actor_user_id: uuid.UUID | None,
        org_id: uuid.UUID | None,
        target_type: str | None,
        target_id: str | None,
        ip_address: str | None,
        details: dict[str, Any],
    ) -> AuditLog:
        row = AuditLog(
            action=action,
            actor_user_id=actor_user_id,
            org_id=org_id,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip_address,
            details=details,
        )
        self.db.add(row)
        return row

    def list_for_org(self, org_id: uuid.UUID, limit: int) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.org_id == org_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt))
