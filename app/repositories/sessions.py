"""Login-session persistence (only token hashes are stored)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.session import UserSession


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        now: datetime,
        expires_at: datetime,
        ip_address: str | None,
        user_agent: str | None,
    ) -> UserSession:
        row = UserSession(
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
            last_seen_at=now,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def get_by_token_hash(self, token_hash: str) -> UserSession | None:
        stmt = select(UserSession).where(UserSession.token_hash == token_hash)
        return self.db.scalars(stmt).unique().one_or_none()

    def get_for_user(self, session_id: uuid.UUID, user_id: uuid.UUID) -> UserSession | None:
        stmt = select(UserSession).where(
            UserSession.id == session_id, UserSession.user_id == user_id
        )
        return self.db.scalars(stmt).unique().one_or_none()

    def list_active_for_user(
        self, user_id: uuid.UUID, now: datetime, limit: int
    ) -> list[UserSession]:
        stmt = (
            select(UserSession)
            .where(
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .order_by(UserSession.last_seen_at.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).unique())

    def revoke(self, row: UserSession, now: datetime, reason: str) -> None:
        if row.revoked_at is None:
            row.revoked_at = now
            row.revoked_reason = reason

    def revoke_all_for_user(
        self, user_id: uuid.UUID, now: datetime, reason: str, *, except_id: uuid.UUID | None = None
    ) -> int:
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )
        if except_id is not None:
            stmt = stmt.where(UserSession.id != except_id)
        result = self.db.execute(stmt)
        return int(getattr(result, "rowcount", 0) or 0)
