# Demo sample service (skeleton)

A tiny stdlib-only Python program that will act as an "instrumented application" sending synthetic
structured logs to TracePilot's ingestion API.

**Phase 1 status:** dry-run only — events are printed as JSON lines; nothing is sent anywhere.
Ingestion keys and `POST /api/v1/ingest/logs` arrive in Phase 3, at which point this skeleton gains
a `--key`/`--base-url` sender and scenarios (error spike, latency regression, outage).

```bash
uv run python demo/sample-service/sample_service.py --dry-run --count 5 --seed 42
```

Event shape (proposed ingestion contract, ARCHITECTURE.md §6): `ts`, `level`, `service`, `message`,
`http_method`, `http_path`, `http_status`, `duration_ms`, `trace_id`, `attributes`.
All data is synthetic; never put real secrets or PII in demo events.
