"""Organization + membership persistence. Every member query is scoped by org_id."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.organization import Organization, OrganizationMember


class OrganizationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, *, name: str, slug: str, created_by: uuid.UUID) -> Organization:
        org = Organization(name=name, slug=slug, created_by=created_by)
        self.db.add(org)
        self.db.flush()
        return org

    def slug_exists(self, slug: str) -> bool:
        return self.db.scalar(select(func.count()).where(Organization.slug == slug)) != 0

    def get(self, org_id: uuid.UUID) -> Organization | None:
        return self.db.get(Organization, org_id)

    def lock(self, org_id: uuid.UUID) -> None:
        """Serialise membership changes of one org (last-owner checks are race-free)."""
        self.db.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())

    def get_membership(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMember | None:
        stmt = select(OrganizationMember).where(
            OrganizationMember.org_id == org_id, OrganizationMember.user_id == user_id
        )
        return self.db.scalars(stmt).unique().one_or_none()

    def list_memberships_for_user(self, user_id: uuid.UUID) -> list[OrganizationMember]:
        stmt = (
            select(OrganizationMember)
            .join(Organization, Organization.id == OrganizationMember.org_id)
            .where(OrganizationMember.user_id == user_id)
            .order_by(Organization.name, Organization.id)
        )
        return list(self.db.scalars(stmt).unique())

    def list_members(self, org_id: uuid.UUID) -> list[OrganizationMember]:
        stmt = (
            select(OrganizationMember)
            .where(OrganizationMember.org_id == org_id)
            .order_by(OrganizationMember.created_at, OrganizationMember.id)
        )
        return list(self.db.scalars(stmt).unique())

    def add_member(self, *, org_id: uuid.UUID, user_id: uuid.UUID, role: str) -> OrganizationMember:
        member = OrganizationMember(org_id=org_id, user_id=user_id, role=role)
        self.db.add(member)
        self.db.flush()
        return member

    def count_owners(self, org_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(
            OrganizationMember.org_id == org_id, OrganizationMember.role == "owner"
        )
        return int(self.db.scalar(stmt) or 0)

    def delete_member(self, member: OrganizationMember) -> None:
        self.db.delete(member)
        self.db.flush()
