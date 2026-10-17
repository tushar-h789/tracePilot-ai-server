"""Log event persistence (bulk, idempotent inserts)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.log_event import LogEvent


class LogEventRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def insert_ignore_duplicates(self, rows: list[dict[str, Any]]) -> int:
        """INSERT ... ON CONFLICT (project_id, event_id) DO NOTHING; returns rows inserted.

        Duplicates inside the same statement are skipped the same way, so a batch that repeats
        an event_id stores it once.
        """
        if not rows:
            return 0
        stmt = (
            insert(LogEvent)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_log_events_project_id_event_id")
            .returning(LogEvent.id)
        )
        return len(self.db.execute(stmt).all())

    def count_for_project(self, project_id: uuid.UUID) -> int:
        stmt = select(func.count()).where(LogEvent.project_id == project_id)
        return int(self.db.scalar(stmt) or 0)
