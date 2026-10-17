from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import Role
from app.models.project import ENVIRONMENT_PATTERN, SLUG_PATTERN
from app.schemas.common import StrictModel


def _not_blank(value: str | None) -> str | None:
    if value is not None and not value.strip():
        raise ValueError("Name must not be blank.")
    return value


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str | None = Field(
        default=None,
        min_length=3,
        max_length=48,
        pattern=SLUG_PATTERN,
        description="Unique within the organization; derived from the name when omitted.",
    )
    environment: str = Field(
        default="production", min_length=1, max_length=32, pattern=ENVIRONMENT_PATTERN
    )
    retention_days: int = Field(default=7, ge=1, le=30)

    _check_name = field_validator("name")(_not_blank)


class ProjectUpdate(StrictModel):
    """Partial update. `org_id`, `archived_at` etc. are not accepted (extra=forbid)."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    slug: str | None = Field(default=None, min_length=3, max_length=48, pattern=SLUG_PATTERN)
    environment: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=ENVIRONMENT_PATTERN
    )
    retention_days: int | None = Field(default=None, ge=1, le=30)

    _check_name = field_validator("name")(_not_blank)


class ProjectOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    slug: str
    environment: str
    retention_days: int
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    role: Role = Field(description="The caller's role (inherited from the organization)")


class ProjectListResponse(BaseModel):
    items: list[ProjectOut]
    next_cursor: str | None = Field(
        description="Opaque cursor for the next page; null when this is the last page"
    )
