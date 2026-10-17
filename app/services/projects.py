"""Projects inside an organization.

Authorization: routes resolve an `OrgScope` (create/list) or a `ProjectScope` (everything
else) from the database before calling this service; nothing here trusts client-provided org
or project ids. Archived projects are read-only and reject ingestion.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.net import ClientInfo
from app.core.pagination import decode_cursor, next_cursor_for
from app.core.permissions import Role
from app.models.project import Project
from app.repositories.projects import ProjectRepository
from app.services.audit import AuditAction, AuditService
from app.services.organizations import OrgScope

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
SLUG_TAKEN = "A project with this slug already exists in the organization."
ARCHIVED_READ_ONLY = "This project is archived. Restore it before making changes."


@dataclass(frozen=True, slots=True)
class ProjectScope:
    """Authorized view of one project for the current user (role inherited from the org)."""

    org_id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID
    role: Role
    project: Project


@dataclass(frozen=True, slots=True)
class ProjectPage:
    items: list[Project]
    next_cursor: str | None


def base_slug(name: str) -> str:
    base = _SLUG_STRIP.sub("-", name.lower()).strip("-")[:40].strip("-")
    return base if len(base) >= 3 else "project"


class ProjectService:
    def __init__(self, db: Session, audit: AuditService) -> None:
        self.db = db
        self.audit = audit
        self.projects = ProjectRepository(db)

    def list(
        self, scope: OrgScope, *, include_archived: bool, cursor: str | None, limit: int
    ) -> ProjectPage:
        rows = self.projects.list_for_org(
            scope.org_id,
            include_archived=include_archived,
            before_id=decode_cursor(cursor),
            limit=limit,
        )
        return ProjectPage(
            items=rows[:limit], next_cursor=next_cursor_for([r.id for r in rows], limit)
        )

    def create(
        self,
        scope: OrgScope,
        *,
        name: str,
        slug: str | None,
        environment: str,
        retention_days: int,
        client: ClientInfo,
    ) -> Project:
        clean = " ".join(name.split())
        if slug is not None:
            if self.projects.slug_exists(scope.org_id, slug):
                raise ConflictError(SLUG_TAKEN)
            final_slug = slug
        else:
            final_slug = base_slug(clean)
            while self.projects.slug_exists(scope.org_id, final_slug):
                final_slug = f"{base_slug(clean)}-{secrets.token_hex(3)}"
        try:
            project = self.projects.add(
                org_id=scope.org_id,
                name=clean,
                slug=final_slug,
                environment=environment,
                retention_days=retention_days,
                created_by=scope.user_id,
            )
        except IntegrityError as exc:  # concurrent create with the same slug
            self.db.rollback()
            raise ConflictError(SLUG_TAKEN) from exc
        self.audit.record(
            self.db,
            AuditAction.PROJECT_CREATED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="project",
            target_id=project.id,
            ip_address=client.ip,
            details={"slug": project.slug, "environment": project.environment},
        )
        self.db.commit()
        return project

    def update(self, scope: ProjectScope, changes: dict[str, Any], client: ClientInfo) -> Project:
        self.projects.lock(scope.project_id)
        project = scope.project
        self.db.refresh(project)
        if project.is_archived:
            raise ConflictError(ARCHIVED_READ_ONLY)
        if "name" in changes and changes["name"] is not None:
            changes["name"] = " ".join(changes["name"].split())
        applied = {
            field: value
            for field, value in changes.items()
            if value is not None and getattr(project, field) != value
        }
        if not applied:
            return project
        new_slug = applied.get("slug")
        if new_slug is not None and self.projects.slug_exists(
            scope.org_id, new_slug, exclude=project.id
        ):
            raise ConflictError(SLUG_TAKEN)
        for field, value in applied.items():
            setattr(project, field, value)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ConflictError(SLUG_TAKEN) from exc
        self.audit.record(
            self.db,
            AuditAction.PROJECT_UPDATED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="project",
            target_id=project.id,
            ip_address=client.ip,
            details={"fields": sorted(applied)},
        )
        self.db.commit()
        self.db.refresh(project)
        return project

    def set_archived(self, scope: ProjectScope, *, archived: bool, client: ClientInfo) -> Project:
        """Archive (soft delete) or restore. Idempotent: repeating the call changes nothing."""
        self.projects.lock(scope.project_id)
        project = scope.project
        self.db.refresh(project)
        if project.is_archived == archived:
            return project
        project.archived_at = datetime.now(UTC) if archived else None
        self.audit.record(
            self.db,
            AuditAction.PROJECT_ARCHIVED if archived else AuditAction.PROJECT_RESTORED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="project",
            target_id=project.id,
            ip_address=client.ip,
        )
        self.db.commit()
        self.db.refresh(project)
        return project
