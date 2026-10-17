"""Log ingestion authenticated by a project ingestion key (never by cookies).

Pipeline: authenticate key -> per-key request rate limit -> strict parse of the whole batch
(any invalid event rejects the batch, nothing stored) -> timestamp window -> per-project event
rate limit -> redact sensitive attribute keys -> one INSERT ... ON CONFLICT DO NOTHING.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import InvalidIngestionKeyError, RateLimitedError, ValidationFailedError
from app.core.ingest_keys import bearer_token, parse_prefix, verify_key
from app.core.logging import redact, scrub_secrets
from app.core.rate_limit import FixedWindowRateLimiter, rate_limit_key
from app.repositories.ingestion_keys import IngestionKeyRepository
from app.repositories.log_events import LogEventRepository
from app.schemas.ingest import IngestBatch, IngestResult, LogEventIn

logger = logging.getLogger("app.ingest")

MAX_REPORTED_ERRORS = 50
_UNAVAILABLE = "Ingestion is temporarily unavailable."


@dataclass(frozen=True, slots=True)
class IngestContext:
    """Who is ingesting: derived only from the ingestion key."""

    key_id: uuid.UUID
    key_prefix: str
    project_id: uuid.UUID
    project_environment: str


def _safe_errors(exc: ValidationError) -> list[dict[str, Any]]:
    # Never echo `input`: payloads may contain secrets.
    return [
        {
            "loc": ["body", *err.get("loc", ())],
            "msg": str(err.get("msg", "")),
            "type": err.get("type"),
        }
        for err in exc.errors()[:MAX_REPORTED_ERRORS]
    ]


class IngestService:
    def __init__(self, db: Session, settings: Settings, limiter: FixedWindowRateLimiter) -> None:
        self.db = db
        self.settings = settings
        self.limiter = limiter
        self.keys = IngestionKeyRepository(db)
        self.events = LogEventRepository(db)
        self._pepper = settings.ingest_key_pepper.get_secret_value().encode()

    # --- authentication ---------------------------------------------------------------------
    def authenticate(self, authorization: str | None) -> IngestContext:
        """Generic 401 for missing/malformed/unknown/revoked/expired keys and archived projects."""
        token = bearer_token(authorization)
        prefix = parse_prefix(token) if token else None
        if token is None or prefix is None:
            logger.info("ingest_auth_failed", extra={"reason": "missing_or_malformed"})
            raise InvalidIngestionKeyError()
        found = self.keys.get_with_project_by_prefix(prefix)
        if found is None:
            logger.info("ingest_auth_failed", extra={"reason": "unknown", "key_prefix": prefix})
            raise InvalidIngestionKeyError()
        key, project = found
        now = datetime.now(UTC)
        reason: str | None = None
        if not verify_key(self._pepper, token, key.key_hash):
            reason = "mismatch"
        elif key.status_at(now) != "active":
            reason = key.status_at(now)
        elif project.archived_at is not None:
            reason = "project_archived"
        if reason is not None:
            logger.info("ingest_auth_failed", extra={"reason": reason, "key_prefix": prefix})
            raise InvalidIngestionKeyError()
        return IngestContext(
            key_id=key.id,
            key_prefix=key.prefix,
            project_id=project.id,
            project_environment=project.environment,
        )

    # --- rate limits ------------------------------------------------------------------------
    def _limit(self, scope: str, identifier: str, limit: int, amount: int) -> None:
        result = self.limiter.hit(
            rate_limit_key(scope, identifier),
            limit=limit,
            window_seconds=self.settings.ingest_rate_limit_window_seconds,
            amount=amount,
            unavailable_message=_UNAVAILABLE,
        )
        if not result.allowed:
            raise RateLimitedError(
                result.retry_after, "Ingestion rate limit exceeded. Retry after the indicated time."
            )

    # --- ingestion --------------------------------------------------------------------------
    def _parse(self, body: bytes) -> list[LogEventIn]:
        try:
            batch = IngestBatch.model_validate_json(body)
        except ValidationError as exc:
            raise ValidationFailedError(
                "The batch is invalid; no events were stored.", errors=_safe_errors(exc)
            ) from exc
        limit = self.settings.ingest_max_batch_events
        if len(batch.events) > limit:
            raise ValidationFailedError(
                f"A batch may contain at most {limit} events; no events were stored.",
                errors=[
                    {
                        "loc": ["body", "events"],
                        "msg": f"At most {limit} events per batch.",
                        "type": "too_long",
                    }
                ],
            )
        return batch.events

    def _check_time_window(self, events: list[LogEventIn], now: datetime) -> None:
        oldest = now - timedelta(seconds=self.settings.ingest_max_past_seconds)
        newest = now + timedelta(seconds=self.settings.ingest_max_future_seconds)
        errors = [
            {
                "loc": ["body", "events", index, "timestamp"],
                "msg": "Timestamp is outside the accepted window "
                f"(at most {self.settings.ingest_max_past_seconds}s old and "
                f"{self.settings.ingest_max_future_seconds}s in the future).",
                "type": "timestamp_out_of_range",
            }
            for index, event in enumerate(events)
            if not oldest <= event.timestamp <= newest
        ]
        if errors:
            raise ValidationFailedError(
                "The batch is invalid; no events were stored.", errors=errors[:MAX_REPORTED_ERRORS]
            )

    def _row(self, ctx: IngestContext, event: LogEventIn) -> dict[str, Any]:
        attributes: dict[str, Any] = redact(event.attributes)
        return {
            "project_id": ctx.project_id,
            "ingestion_key_id": ctx.key_id,
            "event_id": event.event_id,
            "ts": event.timestamp.astimezone(UTC),
            "level": event.level,
            "service": event.service_name,
            "environment": event.environment or ctx.project_environment,
            "message": scrub_secrets(event.message),
            "trace_id": event.trace_id,
            "request_id": event.request_id,
            "http_method": event.http_method,
            "http_path": event.http_path,
            "http_status": event.http_status,
            "duration_ms": event.duration_ms,
            "attributes": attributes,
            "is_synthetic": event.attributes.get("synthetic") is True,
        }

    def ingest(self, ctx: IngestContext, body: bytes) -> IngestResult:
        self._limit(
            "ingest:key", str(ctx.key_id), self.settings.ingest_rate_limit_requests_per_key, 1
        )
        events = self._parse(body)
        now = datetime.now(UTC)
        self._check_time_window(events, now)
        self._limit(
            "ingest:project",
            str(ctx.project_id),
            self.settings.ingest_rate_limit_events_per_project,
            len(events),
        )
        accepted = self.events.insert_ignore_duplicates([self._row(ctx, e) for e in events])
        self.keys.touch(ctx.key_id, now, self.settings.ingest_key_touch_interval_seconds)
        self.db.commit()
        result = IngestResult(
            received=len(events),
            accepted=accepted,
            duplicates=len(events) - accepted,
            rejected=0,
        )
        logger.info(
            "ingest_batch",
            extra={
                "project_id": str(ctx.project_id),
                "key_prefix": ctx.key_prefix,
                "received": result.received,
                "accepted": result.accepted,
                "duplicates": result.duplicates,
            },
        )
        return result
