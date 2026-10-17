# Query performance (Phase 4)

## Method

- Data: the demo project **"Demo Shop API"** in org **"TracePilot Demo (e2e)"** on the local
  compose PostgreSQL 18.6, filled by `BULK=150000 ./scripts/seed-demo.sh`: 900 events through
  the real ingest pipeline plus **150,000 bulk rows** spread over the last 7 days (6 services,
  5 levels, 80 % HTTP requests, ~2.7 % 5xx, message vocabulary of 6 templates). The whole
  `log_events` table had 152,107 rows (52 MB heap), so this project is ~99 % of the table.
- Plans: `uv run python scripts/perf_explain.py --project <id> --out docs/perf/<name>.txt` runs the
  repository methods unchanged, captures each SQL statement with its bound parameters and
  re-runs it under `EXPLAIN (ANALYZE, BUFFERS, COSTS OFF, SUMMARY ON)`. Full plans:
  [`perf/before.txt`](perf/before.txt) (migration 0003 indexes) and
  [`perf/after.txt`](perf/after.txt) (after migration 0004 + `VACUUM ANALYZE`).
- Single run each on a busy developer laptop with a warm cache; treat times as indicative, the
  plan shapes are the point.

## Results

| Query | Before (ms) | Before plan | After (ms) | After plan |
| --- | ---: | --- | ---: | --- |
| logs: default 24 h page | 0.067 | Index Scan `project_id_ts` + Incremental Sort | 0.038 | Index Scan `project_id_ts_id` (no sort) |
| logs: page 2 (keyset cursor) | 0.062 | Index Scan + Incremental Sort, row filter | 0.041 | Index Scan `project_id_ts_id`, cursor in Index Cond |
| logs: level in (error, critical), 7 d | 0.113 | Index Scan `project_id_ts` + Incremental Sort | 0.057 | Index Scan `project_id_errors_ts` (partial) |
| logs: service = payments, 7 d | 0.053 | Index Scan `project_id_service_ts` + Incremental Sort | 0.092 | same (noise) |
| logs: q = 'timeout', 7 d (common word) | 0.142 | Index Scan `project_id_ts` + filter + Incremental Sort | 0.153 | Index Scan `project_id_ts_id` + filter |
| logs: q = 'inventory 503', 7 d | 19.3 | GIN Bitmap Scan + top-N sort (25k matches) | 20.0 | same |
| logs: trace_id exact | 0.021 | Index Scan `project_id_trace_id` | 0.022 | same |
| logs: request_id exact | **14.5** | **Parallel Seq Scan** | **0.017** | Index Scan `project_id_request_id` (partial) |
| facets: services, 24 h | 9.7 | Bitmap Heap Scan | 2.9 | Index Only Scan, 0 heap fetches |
| metrics: totals, 24 h | 12.7 | Bitmap Heap Scan | 7.0 | Index Only Scan `project_id_ts_id`, 0 heap fetches |
| metrics: totals, 7 d | 65.5 | Seq Scan | 66.5 | Seq Scan (correct: whole table in range) |
| metrics: timeseries 24 h (15 min) | 16.3 | Bitmap Heap Scan + sort | 9.8 | Index Only Scan + sort |
| metrics: timeseries 7 d (1 h) | 97.5 | Seq Scan + sort | 109.7 | Seq Scan + sort |
| metrics: per service, 24 h | 17.4 | Bitmap Heap Scan | 12.3 | Index Only Scan |
| metrics: recent errors, 24 h | 0.072 | Index Scan + Incremental Sort, filter | 0.036 | Index Scan `project_id_errors_ts` |
| org: per project, 7 d | 59.3 | Seq Scan | 65.6 | Seq Scan |

## Index changes (migration `0004_log_query_indexes`)

| Index | Change | Why | Size at 152k rows |
| --- | --- | --- | --- |
| `ix_log_events_project_id_ts_id` `(project_id, ts DESC, id DESC) INCLUDE (level, service, environment, http_status, duration_ms)` | replaces `(project_id, ts DESC)` | matches the keyset `ORDER BY ts DESC, id DESC` and the `(ts, id) < cursor` predicate exactly; the INCLUDE columns make the metric aggregates index-only | 13 MB |
| `ix_log_events_project_id_request_id` partial `WHERE request_id IS NOT NULL` | new | request_id lookups were a parallel seq scan (14.5 ms -> 0.02 ms) | 7.4 MB |
| `ix_log_events_project_id_errors_ts` partial `WHERE level IN ('error','critical') OR http_status >= 500` | new | recent errors and error-level filters read only error rows; matters most when errors are rare | 1.6 MB |

Kept: `(project_id, level, ts DESC)`, `(project_id, service, ts DESC)`, the partial trace_id
index and the GIN index on `message_tsv`.

## Observations and limits

- 7-day aggregates are a sequential scan here because the demo project is ~99 % of the table, so
  the planner is right. In a shared table with many tenants the covering index turns the same
  query into an index-only scan over one tenant's range (as the 24 h rows show).
- Aggregates are computed on request, with no rollups. That stays acceptable up to a few million
  rows per project per window. Beyond that the plan is hourly rollup tables maintained by the
  worker (Phase 6+).
- Search for a very common term over 7 days reads all matching rows, then keeps the top 50
  (~20 ms for 25k matches). It is bounded by the 30-day range limit and the 8-token cap. ADR-024
  covers when to add pg_trgm.
- Index-only scans need the visibility map, so autovacuum must keep up. The seed script runs
  `ANALYZE`; the "after" numbers were taken after a manual `VACUUM ANALYZE log_events`.

## Bulk data

The 150,000 bulk rows are **kept** in the demo project so that the dashboard has data to show.
They are marked `is_synthetic = true` and `attributes->>'bulk' = 'true'`, and you can remove
them with:

```sql
DELETE FROM log_events
WHERE project_id = (SELECT p.id FROM projects p JOIN organizations o ON o.id = p.org_id
                    WHERE o.name = 'TracePilot Demo (e2e)' AND p.name = 'Demo Shop API')
  AND attributes->>'bulk' = 'true';
```
