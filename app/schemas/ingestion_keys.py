from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import StrictModel

KeyStatus = Literal["active", "expired", "revoked"]


class IngestionKeyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Name must not be blank.")
        return value


class IngestionKeyRotate(StrictModel):
    grace_seconds: int = Field(
        default=0,
        ge=0,
        le=7 * 86_400,
        description=(
            "How long the old key keeps working (0 = revoked immediately). Capped by "
            "KEY_ROTATION_MAX_GRACE_SECONDS (default 86400)."
        ),
    )


class IngestionKeyOut(BaseModel):
    """Key metadata. The secret is never part of this model."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    prefix: str = Field(description="Public, non-secret identifier (tp_<12 hex>)")
    status: KeyStatus
    created_by: uuid.UUID | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    replaced_by_id: uuid.UUID | None


class IngestionKeyCreated(BaseModel):
    key: IngestionKeyOut
    secret: str = Field(
        description="The full ingestion key. Shown exactly once; only a hash is stored."
    )


class IngestionKeyRotated(IngestionKeyCreated):
    previous_key: IngestionKeyOut


class IngestionKeyListResponse(BaseModel):
    items: list[IngestionKeyOut]
    next_cursor: str | None
