"""Ingestion key persistence. Management queries are scoped by project_id."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models.ingestion_key import IngestionKey
from app.models.project import Project


class IngestionKeyRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self, *, project_id: uuid.UUID, name: str, prefix: str, key_hash: str, created_by: uuid.UUID
    ) -> IngestionKey:
        key = IngestionKey(
            project_id=project_id,
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            created_by=created_by,
        )
        self.db.add(key)
        self.db.flush()
        self.db.refresh(key)
        return key

    def prefix_exists(self, prefix: str) -> bool:
        return self.db.scalar(select(func.count()).where(IngestionKey.prefix == prefix)) != 0

    def get_in_project(self, project_id: uuid.UUID, key_id: uuid.UUID) -> IngestionKey | None:
        stmt = select(IngestionKey).where(
            IngestionKey.id == key_id, IngestionKey.project_id == project_id
        )
        return self.db.scalars(stmt).one_or_none()

    def count_active(self, project_id: uuid.UUID, now: datetime) -> int:
        stmt = select(func.count()).where(
            IngestionKey.project_id == project_id,
            IngestionKey.revoked_at.is_(None),
            or_(IngestionKey.expires_at.is_(None), IngestionKey.expires_at > now),
        )
        return int(self.db.scalar(stmt) or 0)

    def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        include_revoked: bool,
        before_id: uuid.UUID | None,
        limit: int,
    ) -> list[IngestionKey]:
        stmt = select(IngestionKey).where(IngestionKey.project_id == project_id)
        if not include_revoked:
            stmt = stmt.where(IngestionKey.revoked_at.is_(None))
        if before_id is not None:
            stmt = stmt.where(IngestionKey.id < before_id)
        stmt = stmt.order_by(IngestionKey.id.desc()).limit(limit + 1)
        return list(self.db.scalars(stmt))

    def get_with_project_by_prefix(self, prefix: str) -> tuple[IngestionKey, Project] | None:
        """Used only by ingestion auth: one indexed lookup on the public prefix."""
        stmt = (
            select(IngestionKey, Project)
            .join(Project, Project.id == IngestionKey.project_id)
            .where(IngestionKey.prefix == prefix)
        )
        row = self.db.execute(stmt).one_or_none()
        return (row[0], row[1]) if row is not None else None

    def touch(self, key_id: uuid.UUID, now: datetime, interval_seconds: int) -> None:
        """Update last_used_at at most once per interval (one cheap conditional UPDATE)."""
        threshold = now - timedelta(seconds=interval_seconds)
        self.db.execute(
            update(IngestionKey)
            .where(
                IngestionKey.id == key_id,
                or_(IngestionKey.last_used_at.is_(None), IngestionKey.last_used_at < threshold),
            )
            .values(last_used_at=now)
        )
