from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

LEVELS = ("debug", "info", "warning", "error", "critical")


class LogEvent(Base):
    """One ingested log event. Idempotent per project via UNIQUE(project_id, event_id)."""

    __tablename__ = "log_events"
    __table_args__ = (
        UniqueConstraint("project_id", "event_id", name="uq_log_events_project_id_event_id"),
        CheckConstraint(
            "level IN (" + ", ".join(f"'{lvl}'" for lvl in LEVELS) + ")", name="level_valid"
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599", name="http_status_range"
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("char_length(message) <= 65536", name="message_length"),
        Index("ix_log_events_project_id_ts", "project_id", text("ts DESC")),
        Index("ix_log_events_project_id_level_ts", "project_id", "level", text("ts DESC")),
        Index("ix_log_events_project_id_service_ts", "project_id", "service", text("ts DESC")),
        Index(
            "ix_log_events_project_id_trace_id",
            "project_id",
            "trace_id",
            postgresql_where=text("trace_id IS NOT NULL"),
        ),
        Index("ix_log_events_message_tsv", "message_tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    ingestion_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ingestion_keys.id", ondelete="SET NULL")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    http_method: Mapped[str | None] = mapped_column(String(10))
    http_path: Mapped[str | None] = mapped_column(String(512))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"), default=dict
    )
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    message_tsv: Mapped[Any] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', message)", persisted=True)
    )

    def __repr__(self) -> str:
        return f"<LogEvent id={self.id} project_id={self.project_id}>"
