"""Organizations and membership management.

Authorization: routes resolve an `OrgScope` (membership loaded from the DB for the current
user; never from client input). Member-management transitions are re-checked here with the
policy functions in `app.core.permissions`, under a row lock on the organization so that
"keep at least one owner" cannot be raced.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError, ConflictError, NotFoundError, PermissionDeniedError
from app.core.identity import normalize_email
from app.core.net import ClientInfo
from app.core.permissions import (
    Role,
    check_can_add_member,
    check_can_change_role,
    check_can_remove_member,
)
from app.models.audit import AuditLog
from app.models.organization import Organization, OrganizationMember
from app.repositories.audit import AuditRepository
from app.repositories.organizations import OrganizationRepository
from app.repositories.users import UserRepository
from app.services.audit import AuditAction, AuditService

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True, slots=True)
class OrgScope:
    """Authorized view of one organization for the current user (resolved server-side)."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    role: Role


def slugify(name: str) -> str:
    base = _SLUG_STRIP.sub("-", name.lower()).strip("-")[:40].strip("-")
    if len(base) < 2:
        base = "org"
    # Random suffix: no collisions to resolve and slugs do not reveal other orgs' names.
    return f"{base}-{secrets.token_hex(3)}"


class OrganizationService:
    def __init__(self, db: Session, audit: AuditService) -> None:
        self.db = db
        self.audit = audit
        self.orgs = OrganizationRepository(db)
        self.users = UserRepository(db)

    # --- organizations ----------------------------------------------------------------------
    def list_for_user(self, user_id: uuid.UUID) -> list[OrganizationMember]:
        return self.orgs.list_memberships_for_user(user_id)

    def create(self, *, user_id: uuid.UUID, name: str, client: ClientInfo) -> OrganizationMember:
        """Create an org and make the creator its owner, atomically (one transaction)."""
        clean = " ".join(name.split())
        slug = slugify(clean)
        while self.orgs.slug_exists(slug):  # pragma: no cover - 1 in 16M per attempt
            slug = slugify(clean)
        org = self.orgs.add(name=clean, slug=slug, created_by=user_id)
        member = self.orgs.add_member(org_id=org.id, user_id=user_id, role=Role.OWNER.value)
        self.audit.record(
            self.db,
            AuditAction.ORG_CREATED,
            actor_user_id=user_id,
            org_id=org.id,
            target_type="organization",
            target_id=org.id,
            ip_address=client.ip,
        )
        self.db.commit()
        return member

    def get(self, scope: OrgScope) -> Organization:
        org = self.orgs.get(scope.org_id)
        if org is None:  # pragma: no cover - scope resolution already proved existence
            raise NotFoundError("Organization not found.")
        return org

    def rename(self, scope: OrgScope, *, name: str, client: ClientInfo) -> Organization:
        """Rename (ORG_UPDATE: owner/admin). The slug stays stable; the change is audited."""
        org = self.get(scope)
        clean = " ".join(name.split())
        if clean != org.name:
            previous = org.name
            org.name = clean
            self.audit.record(
                self.db,
                AuditAction.ORG_UPDATED,
                actor_user_id=scope.user_id,
                org_id=org.id,
                target_type="organization",
                target_id=org.id,
                ip_address=client.ip,
                details={"name": {"from": previous, "to": clean}},
            )
            self.db.commit()
        return org

    # --- members ----------------------------------------------------------------------------
    def list_members(self, scope: OrgScope) -> list[OrganizationMember]:
        return self.orgs.list_members(scope.org_id)

    def _deny(self, scope: OrgScope, exc: AppError, operation: str, client: ClientInfo) -> None:
        if isinstance(exc, PermissionDeniedError):
            # Release the org row lock first: the independent audit insert references the org
            # (FK -> KEY SHARE lock) and would otherwise wait on our own FOR UPDATE lock.
            self.db.rollback()
            self.audit.record_independent(
                AuditAction.PERMISSION_DENIED,
                actor_user_id=scope.user_id,
                org_id=scope.org_id,
                target_type="organization",
                target_id=scope.org_id,
                ip_address=client.ip,
                details={"operation": operation, "role": scope.role.value},
            )

    def add_member(
        self, scope: OrgScope, *, email: str, role: Role, client: ClientInfo
    ) -> OrganizationMember:
        try:
            check_can_add_member(scope.role, role)
        except PermissionDeniedError as exc:
            self._deny(scope, exc, "member.add", client)
            raise
        self.orgs.lock(scope.org_id)
        user = self.users.get_by_email(normalize_email(email))
        if user is None or not user.is_active:
            # Admins learn whether an email is registered (accepted trade-off, ADR-019).
            raise NotFoundError("No registered user with that email address.")
        if self.orgs.get_membership(scope.org_id, user.id) is not None:
            raise ConflictError("That user is already a member of this organization.")
        try:
            member = self.orgs.add_member(org_id=scope.org_id, user_id=user.id, role=role.value)
        except IntegrityError as exc:  # concurrent add of the same user
            self.db.rollback()
            raise ConflictError("That user is already a member of this organization.") from exc
        self.audit.record(
            self.db,
            AuditAction.MEMBER_ADDED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="user",
            target_id=user.id,
            ip_address=client.ip,
            details={"role": role.value},
        )
        self.db.commit()
        return member

    def _get_target(self, scope: OrgScope, user_id: uuid.UUID) -> OrganizationMember:
        target = self.orgs.get_membership(scope.org_id, user_id)
        if target is None:
            raise NotFoundError("Member not found.")
        return target

    def change_role(
        self, scope: OrgScope, *, user_id: uuid.UUID, new_role: Role, client: ClientInfo
    ) -> OrganizationMember:
        self.orgs.lock(scope.org_id)
        target = self._get_target(scope, user_id)
        old_role = Role(target.role)
        try:
            check_can_change_role(
                actor_role=scope.role,
                target_role=old_role,
                new_role=new_role,
                is_self=user_id == scope.user_id,
                owner_count=self.orgs.count_owners(scope.org_id),
            )
        except PermissionDeniedError as exc:
            self._deny(scope, exc, "member.change_role", client)
            raise
        if old_role is new_role:
            return target
        target.role = new_role.value
        self.audit.record(
            self.db,
            AuditAction.MEMBER_ROLE_CHANGED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="user",
            target_id=user_id,
            ip_address=client.ip,
            details={"from": old_role.value, "to": new_role.value},
        )
        self.db.commit()
        return target

    def remove_member(self, scope: OrgScope, *, user_id: uuid.UUID, client: ClientInfo) -> None:
        self.orgs.lock(scope.org_id)
        target = self._get_target(scope, user_id)
        try:
            check_can_remove_member(
                actor_role=scope.role,
                target_role=Role(target.role),
                owner_count=self.orgs.count_owners(scope.org_id),
            )
        except PermissionDeniedError as exc:
            self._deny(scope, exc, "member.remove", client)
            raise
        role = target.role
        self.orgs.delete_member(target)
        self.audit.record(
            self.db,
            AuditAction.MEMBER_REMOVED,
            actor_user_id=scope.user_id,
            org_id=scope.org_id,
            target_type="user",
            target_id=user_id,
            ip_address=client.ip,
            details={"role": role},
        )
        self.db.commit()

    # --- audit ------------------------------------------------------------------------------
    def list_audit(self, scope: OrgScope, limit: int) -> list[AuditLog]:
        return AuditRepository(self.db).list_for_org(scope.org_id, limit)
