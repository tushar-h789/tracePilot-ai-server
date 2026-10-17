"""Keyset pagination helpers (ADR-022).

List endpoints return ``{"items": [...], "next_cursor": str | null}``. The cursor is an opaque
URL-safe token wrapping the sort key of the last returned row (a UUIDv7 id, newest first).
Cursors are not signed: they only move a window inside the caller's already-authorized scope.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime

from app.core.errors import ValidationFailedError

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
_VERSION = 1


def encode_cursor(last_id: uuid.UUID) -> str:
    raw = json.dumps({"v": _VERSION, "id": str(last_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if cursor is None or cursor == "":
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict) or data.get("v") != _VERSION:
            raise ValueError("bad cursor")
        return uuid.UUID(str(data["id"]))
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailedError(
            "Invalid cursor.",
            errors=[{"loc": ["query", "cursor"], "msg": "Invalid cursor.", "type": "cursor"}],
        ) from exc


def next_cursor_for(ids: list[uuid.UUID], limit: int) -> str | None:
    """`ids` holds up to limit+1 ids; a cursor is returned only if there is another page."""
    if len(ids) <= limit:
        return None
    return encode_cursor(ids[limit - 1])


# --- log events: keyset on (ts DESC, id DESC) ------------------------------------------------
LOG_DEFAULT_LIMIT = 50
LOG_MAX_LIMIT = 200


def encode_log_cursor(ts: datetime, row_id: int) -> str:
    raw = json.dumps(
        {"v": _VERSION, "ts": ts.astimezone(UTC).isoformat(), "id": row_id},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_log_cursor(cursor: str | None) -> tuple[datetime, int] | None:
    """Inverse of `encode_log_cursor`; any malformed value is a 422 (never a 500)."""
    if cursor is None or cursor == "":
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(data, dict) or data.get("v") != _VERSION:
            raise ValueError("bad cursor")
        ts = datetime.fromisoformat(str(data["ts"]))
        row_id = data["id"]
        if ts.tzinfo is None or not isinstance(row_id, int) or isinstance(row_id, bool):
            raise ValueError("bad cursor")
        if row_id < 1:
            raise ValueError("bad cursor")
        return ts, row_id
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValidationFailedError(
            "Invalid cursor.",
            errors=[{"loc": ["query", "cursor"], "msg": "Invalid cursor.", "type": "cursor"}],
        ) from exc
