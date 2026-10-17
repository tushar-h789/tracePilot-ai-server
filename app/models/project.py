from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import created_at_col, updated_at_col, uuid_pk

SLUG_PATTERN = "^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$"
ENVIRONMENT_PATTERN = "^[a-z0-9][a-z0-9_-]{0,31}$"


class Project(Base):
    """A monitored application inside an organization. Archived = soft-deleted (read-only)."""

    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("org_id", "slug", name="uq_projects_org_id_slug"),
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 100", name="name_length"),
        CheckConstraint(f"environment ~ '{ENVIRONMENT_PATTERN}'", name="environment_format"),
        CheckConstraint("retention_days BETWEEN 1 AND 30", name="retention_days_range"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(48), nullable=False)
    environment: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'production'")
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("7"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = updated_at_col()

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def __repr__(self) -> str:
        return f"<Project id={self.id} org_id={self.org_id}>"
