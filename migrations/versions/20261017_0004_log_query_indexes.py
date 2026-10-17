"""Phase 4: indexes for the log explorer and metrics (see docs/PERFORMANCE.md).

- ix_log_events_project_id_ts_id replaces ix_log_events_project_id_ts: (project_id, ts DESC,
  id DESC) matches the keyset ORDER BY exactly (no Incremental Sort) and INCLUDEs the columns
  the metric aggregates and facets read, so they run as index-only scans.
- ix_log_events_project_id_request_id: exact request_id lookups were a parallel seq scan.
- ix_log_events_project_id_errors_ts: partial index for "recent errors", whose cost would
  otherwise grow with the ratio of non-error rows to scan before finding N errors.

Revision ID: 0004_log_query_indexes
Revises: 0003_projects_ingestion
Create Date: 2026-10-17 09:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_log_query_indexes"
down_revision: str | None = "0003_projects_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ERROR_PREDICATE = "level IN ('error', 'critical') OR http_status >= 500"
INCLUDE = ["level", "service", "environment", "http_status", "duration_ms"]


def upgrade() -> None:
    op.create_index(
        "ix_log_events_project_id_ts_id",
        "log_events",
        ["project_id", sa.text("ts DESC"), sa.text("id DESC")],
        postgresql_include=INCLUDE,
    )
    op.drop_index("ix_log_events_project_id_ts", table_name="log_events")
    op.create_index(
        "ix_log_events_project_id_request_id",
        "log_events",
        ["project_id", "request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_index(
        "ix_log_events_project_id_errors_ts",
        "log_events",
        ["project_id", sa.text("ts DESC"), sa.text("id DESC")],
        postgresql_where=sa.text(ERROR_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("ix_log_events_project_id_errors_ts", table_name="log_events")
    op.drop_index("ix_log_events_project_id_request_id", table_name="log_events")
    op.create_index("ix_log_events_project_id_ts", "log_events", ["project_id", sa.text("ts DESC")])
    op.drop_index("ix_log_events_project_id_ts_id", table_name="log_events")
