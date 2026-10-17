# Metrics definitions (Phase 4)

Every number shown in the TracePilot dashboard comes from one of the endpoints below and is
computed in PostgreSQL from the `log_events` table. The SQL predicates live in one place,
`app/repositories/log_queries.py` (`IS_REQUEST`, `IS_ERROR`, `IS_SAMPLE`, `aggregates()`), and
the tests in `tests/test_metrics_api.py` check them against hand-computed fixtures.

## 1. Source and scope

| Item | Rule |
| --- | --- |
| Source table | `log_events` (one row per accepted ingested event; duplicates are dropped at ingest) |
| Timestamp | `ts` is the event's own timestamp, normalised to UTC at ingest (not `received_at`) |
| Time range | Half-open `[from, to)`, both ISO 8601 with an offset. Defaults: `to = now`, `from = to - 24 h`. `from >= to` or a span over **30 days** returns 422 (`loc: ["query","from"]`, `type: "range"`). |
| Project scope | `/projects/{project_id}/metrics/*`: that project only (any role, archived projects included) |
| Organization scope | `/organizations/{org_id}/metrics/*`: all **non-archived** projects of the org (up to 200, newest first) |
| Synthetic data | Included. Rows sent by the demo/SDK carry `is_synthetic = true`; the log explorer shows a badge. |

## 2. Definitions

| Metric | Definition | SQL |
| --- | --- | --- |
| **Events** | Every row in scope | `count(*)` |
| **Request** | An event that carries an HTTP status code. Logs without `http_status` (background jobs, plain app logs) are events but not requests. | `count(*) FILTER (WHERE http_status IS NOT NULL)` |
| **Error** | Level `error` or `critical`, **or** HTTP status `>= 500` | `count(*) FILTER (WHERE level IN ('error','critical') OR http_status >= 500)` |
| **Request errors** | Requests that are errors (numerator of the error rate). An HTTP 4xx is a client error and is **not** counted. | `count(*) FILTER (WHERE http_status IS NOT NULL AND (level IN ('error','critical') OR http_status >= 500))` |
| **Error rate** | `request_errors / requests`, a fraction in `[0, 1]` (the UI shows a percentage) | computed in Python from the two counts |
| **Latency sample** | A request that also carries `duration_ms` | `http_status IS NOT NULL AND duration_ms IS NOT NULL` |
| **p50 / p95 / p99** | Continuous (interpolated) percentiles of `duration_ms` over latency samples, in milliseconds, rounded to 3 decimals | `percentile_cont(ARRAY[0.5,0.95,0.99]) WITHIN GROUP (ORDER BY duration_ms) FILTER (WHERE <sample>)` |

`errors` (all error events, including non-HTTP ones) and `request_errors` are both returned, so
a crash logged by a worker shows up in **Errors** without distorting the request **error rate**.

## 3. Nulls and minimum samples

A metric is `null` (never `0` or a guess) when it cannot be computed honestly; a `reason` field
says why, and the UI shows "—" with that explanation.

| Metric | Null when | `reason` |
| --- | --- | --- |
| `error_rate` | `requests = 0` | `no_requests` (`error_rate_reason`) |
| `p50/p95/p99_ms` (overview, org totals) | `samples = 0` | `no_latency_samples` |
| | `0 < samples < 20` | `insufficient_samples` |
| `p95_ms` (per service, per project) | `samples < 20` | (the row carries `samples`; the response carries `min_samples`) |
| `p95_ms` (each time-series bucket) | `samples < 5` in that bucket | (`samples` per point, `min_samples` on the response) |

Why 20: below that the p95 is essentially the maximum of a handful of values; with 20 samples
it is at least an interpolation between the top two. Why 5 per bucket: buckets are small by
design; a lower bar keeps the chart useful, and the per-bucket sample count is returned so the
UI can show it.

## 4. Time-series buckets

- Bucket size: the smallest of **1 min, 5 min, 15 min, 1 h, 6 h, 1 day** that yields at most
  **200** buckets for the requested span (e.g. 1 h -> 1 min, 24 h -> 15 min, 7 d -> 1 h,
  30 d -> 6 h). `bucket_seconds` is returned.
- Alignment: `date_bin(step, ts, '2000-01-01 00:00:00+00')`, i.e. wall-clock boundaries in UTC.
  The first bucket starts at `date_bin(step, from)`, so it may begin slightly before `from`
  (only rows `>= from` are counted); there can be one extra partial bucket (max 201 points).
- Zero-filled: `generate_series(date_bin(step, from), to - 1 µs, step)` LEFT JOINed with the
  aggregates, so empty buckets are present with `0` counts and `p95_ms = null`.
- Per point: `events`, `requests`, `errors`, `request_errors`, `samples`, `p95_ms`.

## 5. Endpoints

| Endpoint | Returns |
| --- | --- |
| `GET /projects/{id}/metrics/overview` | `totals` (events, requests, errors, request_errors, error_rate(+reason), latency{p50,p95,p99,samples,min_samples,reason}) |
| `GET /projects/{id}/metrics/timeseries` | zero-filled buckets (section 4) |
| `GET /projects/{id}/metrics/services` | per `service_name` (top 50 by events): events, requests, errors, request_errors, error_rate, samples, p95_ms |
| `GET /projects/{id}/metrics/recent-errors?limit=1..50` | newest error events (definition above), default 10 |
| `GET /organizations/{id}/metrics/summary?recent_limit=` | org totals, one row per active project (sorted by events desc), recent errors across projects with `project_name` |
| `GET /organizations/{id}/metrics/timeseries` | zero-filled buckets across active projects |

All take `from` / `to`. All require a session and membership (viewer or higher); a non-member
gets 404, exactly like an unknown id.

## 6. Incidents

Incidents (grouping errors into incidents, AI analysis) arrive in **Phase 5**. The dashboard
shows an explicit empty state instead of a placeholder number.

## 7. Worked example (from `tests/test_metrics_api.py`)

17 events in the window: 10 × `api` HTTP 200 (10..100 ms), 2 × `api` HTTP 500 level error
(200, 300 ms), 1 × `worker` critical (no HTTP), 3 × `worker` info (no HTTP), 1 × `web` HTTP 404
warning (no duration). Then: events 17, requests 13, errors 3, request_errors 2, error rate
2/13 = 15.4 %, latency samples 12 -> percentiles `null` with `insufficient_samples`.
With 25 samples 1..25 ms: p50 = 13, p95 = 1 + 0.95 × 24 = 23.8, p99 = 24.76.
