"""RBAC policy: roles, permissions and the matrix from PROJECT_PLAN §5 (single source of truth).

Dependencies check coarse permissions (`role_has`); services call the `check_*` functions for
member-management transitions (privilege escalation, owner protection). Projects (Phase 3)
inherit the org role, so the project permissions are already defined here.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.errors import ConflictError, PermissionDeniedError


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


ROLE_RANK: dict[Role, int] = {Role.VIEWER: 0, Role.MEMBER: 1, Role.ADMIN: 2, Role.OWNER: 3}


class Permission(StrEnum):
    ORG_READ = "org:read"
    ORG_UPDATE = "org:update"
    ORG_DELETE = "org:delete"
    MEMBERS_READ = "members:read"
    MEMBERS_MANAGE = "members:manage"  # add/remove/change role for admin/member/viewer
    OWNERS_MANAGE = "owners:manage"  # grant/revoke owner, modify/remove owners
    AUDIT_READ = "audit:read"
    # Phase 3+ (inherited by projects; defined now so the matrix lives in one place)
    PROJECT_READ = "project:read"
    PROJECT_MANAGE = "project:manage"
    KEYS_READ_METADATA = "keys:read_metadata"
    KEYS_MANAGE = "keys:manage"
    RULES_MANAGE = "rules:manage"
    INCIDENT_ACT = "incident:act"
    INVESTIGATION_START = "investigation:start"


_ALL = frozenset(Permission)
_ADMIN = _ALL - {Permission.ORG_DELETE, Permission.OWNERS_MANAGE}
_MEMBER = frozenset(
    {
        Permission.ORG_READ,
        Permission.MEMBERS_READ,
        Permission.PROJECT_READ,
        Permission.KEYS_READ_METADATA,
        Permission.INCIDENT_ACT,
        Permission.INVESTIGATION_START,
    }
)
_VIEWER = frozenset({Permission.ORG_READ, Permission.MEMBERS_READ, Permission.PROJECT_READ})

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _ALL,
    Role.ADMIN: _ADMIN,
    Role.MEMBER: _MEMBER,
    Role.VIEWER: _VIEWER,
}

# Denials of these are written to the audit log (security-sensitive operations).
SENSITIVE_PERMISSIONS = frozenset(
    {
        Permission.ORG_UPDATE,
        Permission.ORG_DELETE,
        Permission.MEMBERS_MANAGE,
        Permission.OWNERS_MANAGE,
        Permission.AUDIT_READ,
        Permission.KEYS_MANAGE,
        Permission.PROJECT_MANAGE,
    }
)


def role_has(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[role]


def _require(role: Role, permission: Permission, message: str | None = None) -> None:
    if not role_has(role, permission):
        raise PermissionDeniedError(message) if message else PermissionDeniedError()


def check_can_add_member(actor_role: Role, new_role: Role) -> None:
    _require(actor_role, Permission.MEMBERS_MANAGE)
    if new_role is Role.OWNER:
        _require(actor_role, Permission.OWNERS_MANAGE, "Only owners can grant the owner role.")


def check_can_change_role(
    *,
    actor_role: Role,
    target_role: Role,
    new_role: Role,
    is_self: bool,
    owner_count: int,
) -> None:
    _require(actor_role, Permission.MEMBERS_MANAGE)
    if target_role is Role.OWNER:
        _require(actor_role, Permission.OWNERS_MANAGE, "Only owners can modify an owner.")
    if new_role is Role.OWNER:
        _require(actor_role, Permission.OWNERS_MANAGE, "Only owners can grant the owner role.")
    if is_self and ROLE_RANK[new_role] > ROLE_RANK[actor_role]:
        raise PermissionDeniedError("You cannot raise your own role.")
    if target_role is Role.OWNER and new_role is not Role.OWNER and owner_count <= 1:
        raise ConflictError("An organization must keep at least one owner.")


def check_can_remove_member(*, actor_role: Role, target_role: Role, owner_count: int) -> None:
    _require(actor_role, Permission.MEMBERS_MANAGE)
    if target_role is Role.OWNER:
        _require(actor_role, Permission.OWNERS_MANAGE, "Only owners can remove an owner.")
        if owner_count <= 1:
            raise ConflictError("An organization must keep at least one owner.")
