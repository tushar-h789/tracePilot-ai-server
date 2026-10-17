"""EXPLAIN (ANALYZE, BUFFERS) for every Phase 4 read query, exactly as the app issues it.

The repository methods run normally; a SQLAlchemy `before_cursor_execute` hook captures each
statement with its bound parameters, which is then re-run under EXPLAIN on the same connection.

    uv run python scripts/perf_explain.py --project <uuid> [--out docs/perf/after.txt]

DATABASE_URL comes from the environment or .env (read-only queries; nothing is written).
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.time_range import TimeRange, bucket_step
from app.models.log_event import LogEvent
from app.repositories.log_queries import (
    LogFilters,
    LogQueryRepository,
    search_tsquery,
)


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1]
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    return url


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=uuid.UUID)
    parser.add_argument("--out")
    args = parser.parse_args()
    pid: uuid.UUID = args.project

    engine = create_engine(database_url())
    captured: list[tuple[str, Any]] = []

    @event.listens_for(engine, "before_cursor_execute")
    def capture(_conn: Any, _cur: Any, statement: str, params: Any, _ctx: Any, _many: bool) -> None:
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            captured.append((statement, params))

    now = datetime.now(UTC)
    day = TimeRange(now - timedelta(hours=24), now)
    week = TimeRange(now - timedelta(days=7), now)
    out: list[str] = []

    with Session(engine) as db:
        repo = LogQueryRepository(db)
        first_page = repo.list_logs(pid, day, LogFilters(), before=None, limit=50)
        mid = first_page[-1] if first_page else None
        cases: list[tuple[str, Any]] = [
            (
                "logs: default 24h page",
                lambda: repo.list_logs(pid, day, LogFilters(), before=None, limit=50),
            ),
            (
                "logs: page 2 (keyset cursor)",
                lambda: repo.list_logs(
                    pid, day, LogFilters(), before=(mid.ts, mid.id) if mid else None, limit=50
                ),
            ),
            (
                "logs: level=error,critical (7d)",
                lambda: repo.list_logs(
                    pid, week, LogFilters(levels=["critical", "error"]), before=None, limit=50
                ),
            ),
            (
                "logs: service=payments (7d)",
                lambda: repo.list_logs(
                    pid, week, LogFilters(services=["payments"]), before=None, limit=50
                ),
            ),
            (
                "logs: q='timeout' (7d)",
                lambda: repo.list_logs(
                    pid, week, LogFilters(tsquery=search_tsquery("timeout")), before=None, limit=50
                ),
            ),
            (
                "logs: q='inventory 503' rare-ish (7d)",
                lambda: repo.list_logs(
                    pid,
                    week,
                    LogFilters(tsquery=search_tsquery("inventory 503")),
                    before=None,
                    limit=50,
                ),
            ),
            (
                "logs: trace_id exact (7d)",
                lambda: repo.list_logs(
                    pid,
                    week,
                    LogFilters(trace_id="c4ca4238a0b923820dcc509a6f75849b"),
                    before=None,
                    limit=50,
                ),
            ),
            (
                "logs: request_id exact (7d)",
                lambda: repo.list_logs(
                    pid, week, LogFilters(request_id="bulk-77777"), before=None, limit=50
                ),
            ),
            ("facets: services (24h)", lambda: repo.facet(LogEvent.service, pid, day, 100)),
            ("metrics: totals (24h)", lambda: repo.totals([pid], day)),
            ("metrics: totals (7d)", lambda: repo.totals([pid], week)),
            (
                "metrics: timeseries (24h, 15m buckets)",
                lambda: repo.timeseries([pid], day, bucket_step(day.span)),
            ),
            (
                "metrics: timeseries (7d, 1h buckets)",
                lambda: repo.timeseries([pid], week, bucket_step(week.span)),
            ),
            ("metrics: per service (24h)", lambda: repo.per_service(pid, day, 50)),
            ("metrics: recent errors (24h)", lambda: repo.recent_errors([pid], day, 10)),
            ("org: per project (7d)", lambda: repo.per_project([pid], week)),
        ]
        conn = db.connection()
        for title, run in cases:
            captured.clear()
            run()
            statement, params = captured[-1]
            rows = conn.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY ON) " + statement, params
            ).all()
            out.append(f"### {title}\n" + "\n".join(r[0] for r in rows) + "\n")
    text = "\n".join(out)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    print(text)


if __name__ == "__main__":
    main()
