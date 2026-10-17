"""TracePilot demo "sample service": emits synthetic structured log events.

Phase 1 skeleton: only `--dry-run` is supported, printing JSON lines locally. Sending to the
ingestion API (POST /api/v1/ingest/logs with a project ingestion key) arrives in Phase 3.

    python demo/sample-service/sample_service.py --dry-run --count 5 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

SERVICES = ("checkout", "payments", "catalog")
ROUTES = (
    ("GET", "/api/products"),
    ("GET", "/api/products/{id}"),
    ("POST", "/api/cart/items"),
    ("POST", "/api/checkout"),
)


def make_event(rng: random.Random, now: datetime) -> dict[str, Any]:
    method, path = rng.choice(ROUTES)
    status = rng.choices((200, 201, 404, 500, 503), weights=(80, 8, 6, 4, 2))[0]
    level = "error" if status >= 500 else ("warn" if status >= 400 else "info")
    duration = round(rng.lognormvariate(3.5, 0.6), 1)
    return {
        "ts": (now - timedelta(milliseconds=rng.randint(0, 60_000))).isoformat(),
        "level": level,
        "service": rng.choice(SERVICES),
        "message": f"{method} {path} -> {status} in {duration}ms",
        "http_method": method,
        "http_path": path,
        "http_status": status,
        "duration_ms": duration,
        "trace_id": uuid.UUID(int=rng.getrandbits(128)).hex,
        "attributes": {"region": "local", "synthetic": True},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic log emitter (demo skeleton)")
    parser.add_argument("--dry-run", action="store_true", help="print events instead of sending")
    parser.add_argument("--count", type=int, default=10, help="number of events (1-1000)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible output")
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 1000:
        parser.error("--count must be between 1 and 1000")
    if not args.dry_run:
        print("Sending is not implemented yet (Phase 3: ingestion keys). Use --dry-run.")
        return 2
    rng = random.Random(args.seed)
    now = datetime.now(UTC)
    for _ in range(args.count):
        sys.stdout.write(json.dumps(make_event(rng, now)) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
