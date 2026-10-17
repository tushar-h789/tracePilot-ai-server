from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.permissions import Role
from app.schemas.common import StrictModel


class OrganizationCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank.")
        return value


class OrganizationOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    role: Role = Field(description="The caller's role in this organization")
    created_at: datetime


class OrganizationListResponse(BaseModel):
    items: list[OrganizationOut]


class MemberAdd(StrictModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role = Role.MEMBER


class MemberUpdate(StrictModel):
    role: Role


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    joined_at: datetime


class MemberListResponse(BaseModel):
    items: list[MemberOut]


class AuditLogOut(BaseModel):
    id: uuid.UUID
    action: str
    actor_user_id: uuid.UUID | None
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any]
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogOut]
