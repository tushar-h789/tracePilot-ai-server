# sample-service: synthetic log generator

A small, **deterministic** traffic simulator for a fictional shop (`web-frontend`,
`checkout-api`, `payments-worker`). It sends logs to TracePilot through the
[`tracepilot-sdk`](../../packages/python-sdk). It is a separate uv project and is not part of
the server image.

Every event is clearly marked as synthetic:

- `environment = "demo"`
- `attributes.synthetic = true`. The API stores this as `log_events.is_synthetic`.
- `attributes.scenario` and `attributes.seed`

## Scenarios

| Scenario | What it simulates |
|---|---|
| `normal` | Healthy baseline: ~1% errors, ~80 ms median latency |
| `latency` | Latency regression on `checkout-api`: ~900 ms median with a long tail (other services stay ~80 ms) |
| `http-500` | Repeated HTTP 500s on `checkout-api` (~40% of its requests) |
| `db-timeout` | Database timeouts in `payments-worker` (~30% of its requests, HTTP 504, ≥5 s, `DatabaseTimeout`) |
| `external-api-failure` | Payment provider failing: HTTP 502 `UpstreamError` from `payments-worker` (~35%) |
| `recovery` | An incident that resolves: `checkout-api` errors fall 30% → 1% and latency 600 → 80 ms across the window |

## Usage

```bash
cd demo/sample-service
uv sync

# Preview without sending (JSON lines on stdout)
uv run tracepilot-demo --scenario http-500 --seed 7 --count 5 --dry-run

# Send to a local stack. The key comes ONLY from the environment, never from a flag.
export TRACEPILOT_INGEST_KEY='tp_...'      # shown once when you create a key in the UI or API
uv run tracepilot-demo --scenario db-timeout --seed 42 \
  --start 2026-10-17T06:00:00Z --duration 600 --count 500 \
  --endpoint http://localhost:8000
# -> {"scenario": "db-timeout", "seed": 42, ..., "accepted": 500, "duplicates": 0}
```

| Option | Default | Meaning |
|---|---|---|
| `--scenario` | `normal` | One of the scenarios above |
| `--seed` | `42` | RNG seed |
| `--start` | now − duration, rounded down to the minute | Window start (ISO 8601 **with timezone**) |
| `--duration` | `300` | Window length in seconds (≤ 7 days) |
| `--count` | `200` | Number of events (≤ 100 000) |
| `--batch-size` | `500` | Events per HTTP request (the SDK splits further by bytes) |
| `--dry-run` | off | Print events and send nothing. No key needed. |
| `--endpoint` | `$TRACEPILOT_ENDPOINT` or `http://localhost:8000` | API base URL |

Exit codes: `0` ok, `1` API or transport error (the message never contains the key),
`2` usage error or missing `TRACEPILOT_INGEST_KEY`.

## Determinism and idempotency

- **Event ids** are `uuid5(NAMESPACE, f"{scenario}:{seed}:{index}")`, so they depend only on
  scenario, seed and index, not on `--start`.
- **All other fields** come from `random.Random(f"tracepilot-demo:{scenario}:{seed}")`, plus
  timestamps evenly spaced from `--start`.
- The same `--scenario/--seed/--start/--duration/--count` therefore always produces
  byte-identical output (see the tests).
- Sending the same scenario and seed twice to the same project stores nothing new. The API
  deduplicates on `(project_id, event_id)`, so the rerun reports `"duplicates": N`.
  This holds even if `--start` changes.
- Use a different seed to get new rows.

> The ingest API accepts timestamps from up to 7 days in the past and 5 minutes in the future.
> A fixed `--start` older than that is rejected with 422.

## Development

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest --cov
```

The tests cover:

- same seed gives identical output, and different seeds give different output;
- scenario characteristics (error rates, latency medians and tails, error types, recovery trend);
- demo tagging and the window bounds;
- the CLI: dry-run, missing key, bad arguments;
- sending through the SDK against a mocked API, including idempotent reruns and keeping the
  key out of output.
