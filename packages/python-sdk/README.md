# tracepilot-sdk (Python)

Small, typed client for the TracePilot AI ingestion API (`POST /api/v1/ingest/logs`).
Depends only on `httpx` and `pydantic`. Python ≥ 3.11.

## Install (from this repository)

```bash
uv add --editable ./packages/python-sdk          # from another uv project
# or: pip install ./packages/python-sdk
```

## Usage

```python
from tracepilot_sdk import LogEvent, TracePilotClient

# Key and endpoint can also come from TRACEPILOT_INGEST_KEY / TRACEPILOT_ENDPOINT.
with TracePilotClient(endpoint="http://localhost:8000") as client:
    result = client.send(
        [
            LogEvent(
                service_name="checkout",
                level="error",
                message="POST /api/checkout -> 500",
                http_method="POST",
                http_path="/api/checkout",
                http_status=500,
                duration_ms=812.4,
                trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                attributes={"region": "eu-west-1"},
            ),
        ]
    )
print(result.accepted, result.duplicates)
```

Dicts are accepted too and validated locally against the same contract before sending.

## Behaviour

| Topic | Behaviour |
|---|---|
| Auth | `Authorization: Bearer <ingestion key>`; cookies are never used. |
| Key safety | The key is read from the argument or `TRACEPILOT_INGEST_KEY`, never logged; `repr(client)` shows only `tp_<public id>_***`; error messages never include it. |
| Timeouts | 5 s connect, 10 s overall per request (configurable). |
| Retries | Up to `max_retries=3` extra attempts for connect errors, timeouts, HTTP 429 and 5xx. Exponential backoff `0.5 s · 2^n` capped at 8 s with full jitter; 429 waits exactly `Retry-After` (raised immediately if it exceeds `max_retry_after`, 60 s). |
| Never retried | 400/401/403/404/413/415/422 — the request will not succeed by repeating it. |
| Idempotency | Every event has an `event_id` (UUID4 by default). The server stores each `(project, event_id)` once, so retries and re-sends cannot create duplicates. Use deterministic ids (e.g. `uuid5`) to make replays idempotent across processes. |
| Batching | `send()` splits into batches of ≤ 500 events and ≤ 1,000,000 bytes (server limits: 500 events, 1 MiB). |
| Result | `IngestResult(received, accepted, duplicates, rejected, batches)` summed over batches. |

## Errors

```
TracePilotError
├── ConfigurationError      missing key, bad endpoint/limits
├── TransportError          network failure or timeout after retries
└── ApiError                (status_code, code)
    ├── AuthenticationError 401 – unknown/revoked/expired key or archived project
    ├── ValidationError     400/413/415/422 – `errors` lists field problems; nothing was stored
    ├── RateLimitError      429 after retries – `retry_after`
    └── ServerError         5xx after retries
```

## Event contract

`event_id` (UUID), `timestamp` (timezone-aware; stored as UTC; must be within the last 7 days and
≤ 5 min in the future), `level` (`debug|info|warning|error|critical`), `service_name`,
`environment` (optional, defaults to the project's), `message` (≤ 8 KiB), `trace_id`,
`request_id`, `duration_ms` (≥ 0), `http_method`, `http_path`, `http_status` (100–599),
`attributes` (JSON object, depth ≤ 5, ≤ 128 keys, ≤ 16 KiB). Unknown fields are rejected —
including `project_id`: the project is derived from the key only.

## Development

```bash
cd packages/python-sdk
uv sync --locked
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest --cov
```
