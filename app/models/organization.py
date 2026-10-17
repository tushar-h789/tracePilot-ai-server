from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import created_at_col, updated_at_col, uuid_pk
from app.models.user import User

ROLE_VALUES = ("owner", "admin", "member", "viewer")


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$'", name="slug_format"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 100", name="name_length"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(48), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    def __repr__(self) -> str:
        return f"<Organization id={self.id}>"


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_organization_members_org_id_user_id"),
        CheckConstraint(
            "role IN (" + ", ".join(f"'{r}'" for r in ROLE_VALUES) + ")", name="role_valid"
        ),
        Index("ix_organization_members_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    user: Mapped[User] = relationship(lazy="joined", innerjoin=True)
    organization: Mapped[Organization] = relationship(lazy="joined", innerjoin=True)

    def __repr__(self) -> str:
        return f"<OrganizationMember org_id={self.org_id} user_id={self.user_id} role={self.role}>"
