"""Organization + member routes (thin). Scope is always resolved from the DB membership."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ClientDep, CurrentAuth, OrgServiceDep, require_csrf, require_org_permission
from app.core.permissions import Permission, Role
from app.models.organization import OrganizationMember
from app.schemas.common import problem_responses
from app.schemas.organizations import (
    AuditLogListResponse,
    AuditLogOut,
    MemberAdd,
    MemberListResponse,
    MemberOut,
    MemberUpdate,
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationOut,
)
from app.services.organizations import OrgScope

router = APIRouter(
    prefix="/organizations", tags=["organizations"], dependencies=[Depends(require_csrf)]
)

OrgRead = Annotated[OrgScope, Depends(require_org_permission(Permission.ORG_READ))]
MembersRead = Annotated[OrgScope, Depends(require_org_permission(Permission.MEMBERS_READ))]
MembersManage = Annotated[OrgScope, Depends(require_org_permission(Permission.MEMBERS_MANAGE))]
AuditRead = Annotated[OrgScope, Depends(require_org_permission(Permission.AUDIT_READ))]


def _org_out(member: OrganizationMember) -> OrganizationOut:
    org = member.organization
    return OrganizationOut(
        id=org.id, name=org.name, slug=org.slug, role=Role(member.role), created_at=org.created_at
    )


def _member_out(member: OrganizationMember) -> MemberOut:
    return MemberOut(
        user_id=member.user_id,
        email=member.user.email,
        display_name=member.user.display_name,
        role=Role(member.role),
        joined_at=member.created_at,
    )


@router.get(
    "",
    response_model=OrganizationListResponse,
    summary="List my organizations",
    responses=problem_responses(401),
)
def list_organizations(auth: CurrentAuth, service: OrgServiceDep) -> OrganizationListResponse:
    return OrganizationListResponse(
        items=[_org_out(m) for m in service.list_for_user(auth.user.id)]
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=OrganizationOut,
    summary="Create an organization (the creator becomes owner)",
    responses=problem_responses(401, 403, 422),
)
def create_organization(
    body: OrganizationCreate, auth: CurrentAuth, service: OrgServiceDep, client: ClientDep
) -> OrganizationOut:
    return _org_out(service.create(user_id=auth.user.id, name=body.name, client=client))


@router.get(
    "/{org_id}",
    response_model=OrganizationOut,
    summary="Get one of my organizations",
    responses=problem_responses(401, 404),
)
def get_organization(scope: OrgRead, service: OrgServiceDep) -> OrganizationOut:
    org = service.get(scope)
    return OrganizationOut(
        id=org.id, name=org.name, slug=org.slug, role=scope.role, created_at=org.created_at
    )


@router.get(
    "/{org_id}/members",
    response_model=MemberListResponse,
    summary="List members",
    responses=problem_responses(401, 404),
)
def list_members(scope: MembersRead, service: OrgServiceDep) -> MemberListResponse:
    return MemberListResponse(items=[_member_out(m) for m in service.list_members(scope)])


@router.post(
    "/{org_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberOut,
    summary="Add an existing user by email (admin+; only owners may grant owner)",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def add_member(
    body: MemberAdd, scope: MembersManage, service: OrgServiceDep, client: ClientDep
) -> MemberOut:
    return _member_out(service.add_member(scope, email=body.email, role=body.role, client=client))


@router.patch(
    "/{org_id}/members/{user_id}",
    response_model=MemberOut,
    summary="Change a member's role",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def change_member_role(
    user_id: uuid.UUID,
    body: MemberUpdate,
    scope: MembersManage,
    service: OrgServiceDep,
    client: ClientDep,
) -> MemberOut:
    return _member_out(
        service.change_role(scope, user_id=user_id, new_role=body.role, client=client)
    )


@router.delete(
    "/{org_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
    responses=problem_responses(401, 403, 404, 409),
)
def remove_member(
    user_id: uuid.UUID, scope: MembersManage, service: OrgServiceDep, client: ClientDep
) -> None:
    service.remove_member(scope, user_id=user_id, client=client)


@router.get(
    "/{org_id}/audit-logs",
    response_model=AuditLogListResponse,
    summary="Recent audit events for the organization (admin+)",
    responses=problem_responses(401, 403, 404),
)
def list_audit_logs(
    scope: AuditRead,
    service: OrgServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditLogListResponse:
    return AuditLogListResponse(
        items=[
            AuditLogOut(
                id=row.id,
                action=row.action,
                actor_user_id=row.actor_user_id,
                target_type=row.target_type,
                target_id=row.target_id,
                metadata=row.details,
                created_at=row.created_at,
            )
            for row in service.list_audit(scope, limit)
        ]
    )
