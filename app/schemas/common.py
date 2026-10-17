from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Request bodies: unknown fields are rejected (no mass assignment of role/org_id/etc.)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class FieldError(BaseModel):
    loc: list[str | int]
    msg: str
    type: str | None = None


class Problem(BaseModel):
    """RFC 9457 problem details (application/problem+json)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    code: str | None = None
    request_id: str | None = None
    errors: list[FieldError] | None = None

    model_config = ConfigDict(json_schema_extra={"additionalProperties": True})


class MessageResponse(BaseModel):
    detail: str


def problem_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    descriptions = {
        401: "Not authenticated",
        403: "Forbidden (CSRF failure or insufficient role)",
        404: "Not found (or not visible to the caller)",
        409: "Conflict",
        422: "Validation failed",
        429: "Rate limited (see Retry-After)",
        503: "Dependency unavailable",
    }
    return {
        code: {
            "model": Problem,
            "description": descriptions.get(code, "Error"),
            "content": {"application/problem+json": {}},
        }
        for code in codes
    }
