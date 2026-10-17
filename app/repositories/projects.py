"""Project persistence. Every query is scoped by org_id or by an already-authorized project id."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.project import Project


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, project_id: uuid.UUID) -> Project | None:
        return self.db.get(Project, project_id)

    def lock(self, project_id: uuid.UUID) -> None:
        self.db.execute(select(Project.id).where(Project.id == project_id).with_for_update())

    def slug_exists(
        self, org_id: uuid.UUID, slug: str, *, exclude: uuid.UUID | None = None
    ) -> bool:
        stmt = select(func.count()).where(Project.org_id == org_id, Project.slug == slug)
        if exclude is not None:
            stmt = stmt.where(Project.id != exclude)
        return self.db.scalar(stmt) != 0

    def add(
        self,
        *,
        org_id: uuid.UUID,
        name: str,
        slug: str,
        environment: str,
        retention_days: int,
        created_by: uuid.UUID,
    ) -> Project:
        project = Project(
            org_id=org_id,
            name=name,
            slug=slug,
            environment=environment,
            retention_days=retention_days,
            created_by=created_by,
        )
        self.db.add(project)
        self.db.flush()
        self.db.refresh(project)
        return project

    def list_for_org(
        self,
        org_id: uuid.UUID,
        *,
        include_archived: bool,
        before_id: uuid.UUID | None,
        limit: int,
    ) -> list[Project]:
        """Newest first (UUIDv7 ids are time-ordered); limit+1 rows reveal a next page."""
        stmt = select(Project).where(Project.org_id == org_id)
        if not include_archived:
            stmt = stmt.where(Project.archived_at.is_(None))
        if before_id is not None:
            stmt = stmt.where(Project.id < before_id)
        stmt = stmt.order_by(Project.id.desc()).limit(limit + 1)
        return list(self.db.scalars(stmt))

    def list_active(self, org_id: uuid.UUID, limit: int) -> list[Project]:
        """Non-archived projects of an org, newest first (org dashboards)."""
        stmt = (
            select(Project)
            .where(Project.org_id == org_id, Project.archived_at.is_(None))
            .order_by(Project.id.desc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())
