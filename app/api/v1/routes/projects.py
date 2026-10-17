"""Project routes (thin). Org scope comes from the path + DB membership, never from the body."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import (
    ClientDep,
    ProjectServiceDep,
    require_csrf,
    require_org_permission,
    require_project_permission,
)
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.core.permissions import Permission, Role
from app.models.project import Project
from app.schemas.common import problem_responses
from app.schemas.projects import ProjectCreate, ProjectListResponse, ProjectOut, ProjectUpdate
from app.services.organizations import OrgScope
from app.services.projects import ProjectScope

router = APIRouter(tags=["projects"], dependencies=[Depends(require_csrf)])

OrgProjectsRead = Annotated[OrgScope, Depends(require_org_permission(Permission.PROJECT_READ))]
OrgProjectsManage = Annotated[OrgScope, Depends(require_org_permission(Permission.PROJECT_MANAGE))]
ProjectRead = Annotated[ProjectScope, Depends(require_project_permission(Permission.PROJECT_READ))]
ProjectManage = Annotated[
    ProjectScope, Depends(require_project_permission(Permission.PROJECT_MANAGE))
]


def project_out(project: Project, role: Role) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        org_id=project.org_id,
        name=project.name,
        slug=project.slug,
        environment=project.environment,
        retention_days=project.retention_days,
        archived_at=project.archived_at,
        created_at=project.created_at,
        updated_at=project.updated_at,
        role=role,
    )


@router.get(
    "/organizations/{org_id}/projects",
    response_model=ProjectListResponse,
    summary="List projects in an organization (newest first, keyset pagination)",
    responses=problem_responses(401, 404, 422),
)
def list_projects(
    scope: OrgProjectsRead,
    service: ProjectServiceDep,
    include_archived: bool = False,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> ProjectListResponse:
    page = service.list(scope, include_archived=include_archived, cursor=cursor, limit=limit)
    return ProjectListResponse(
        items=[project_out(p, scope.role) for p in page.items], next_cursor=page.next_cursor
    )


@router.post(
    "/organizations/{org_id}/projects",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectOut,
    summary="Create a project (owner/admin)",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def create_project(
    body: ProjectCreate, scope: OrgProjectsManage, service: ProjectServiceDep, client: ClientDep
) -> ProjectOut:
    project = service.create(
        scope,
        name=body.name,
        slug=body.slug,
        environment=body.environment,
        retention_days=body.retention_days,
        client=client,
    )
    return project_out(project, scope.role)


@router.get(
    "/projects/{project_id}",
    response_model=ProjectOut,
    summary="Get a project (any member of its organization)",
    responses=problem_responses(401, 404, 422),
)
def get_project(scope: ProjectRead) -> ProjectOut:
    return project_out(scope.project, scope.role)


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectOut,
    summary="Update a project (owner/admin; archived projects are read-only)",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def update_project(
    body: ProjectUpdate, scope: ProjectManage, service: ProjectServiceDep, client: ClientDep
) -> ProjectOut:
    project = service.update(scope, body.model_dump(exclude_unset=True), client)
    return project_out(project, scope.role)


@router.post(
    "/projects/{project_id}/archive",
    response_model=ProjectOut,
    summary="Archive a project (owner/admin): hidden from default lists, ingestion rejected",
    responses=problem_responses(401, 403, 404, 422),
)
def archive_project(
    scope: ProjectManage, service: ProjectServiceDep, client: ClientDep
) -> ProjectOut:
    return project_out(service.set_archived(scope, archived=True, client=client), scope.role)


@router.post(
    "/projects/{project_id}/restore",
    response_model=ProjectOut,
    summary="Restore an archived project (owner/admin)",
    responses=problem_responses(401, 403, 404, 422),
)
def restore_project(
    scope: ProjectManage, service: ProjectServiceDep, client: ClientDep
) -> ProjectOut:
    return project_out(service.set_archived(scope, archived=False, client=client), scope.role)
