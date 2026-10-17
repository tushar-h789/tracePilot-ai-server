from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel


class RegisterRequest(StrictModel):
    # Generous schema bounds; the password policy (12..128) is enforced by the service so the
    # error message is specific and the raw value is never echoed back.
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=100)


class LoginRequest(StrictModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime


class SessionInfo(BaseModel):
    expires_at: datetime
    idle_timeout_seconds: int


class MeResponse(BaseModel):
    user: UserOut
    session: SessionInfo


class CsrfResponse(BaseModel):
    """The token is also set as the readable CSRF cookie; send it back in `X-CSRF-Token`."""

    csrf_token: str
    header_name: str = "X-CSRF-Token"


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip_address: str | None
    user_agent: str | None
    current: bool


class SessionListResponse(BaseModel):
    items: list[SessionOut]


class LogoutAllResponse(BaseModel):
    revoked_sessions: int
