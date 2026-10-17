"""Command line entry point: `tracepilot-demo` / `python -m sample_service`.

The ingestion key is read ONLY from the TRACEPILOT_INGEST_KEY environment variable (never a
flag, so it does not end up in shell history or process listings) and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TextIO

from tracepilot_sdk import TracePilotClient, TracePilotError

from sample_service.scenarios import SCENARIOS, generate_events

ENV_KEY = "TRACEPILOT_INGEST_KEY"
ENV_ENDPOINT = "TRACEPILOT_ENDPOINT"
MAX_COUNT = 100_000

ClientFactory = Callable[[str, str], TracePilotClient]


def _aware(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("must include a timezone, e.g. 2026-10-17T07:00:00Z")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tracepilot-demo",
        description="Send deterministic synthetic logs to TracePilot "
        "(ingestion key via TRACEPILOT_INGEST_KEY).",
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument(
        "--start",
        type=_aware,
        default=None,
        help="Window start (ISO 8601 with timezone). Default: now minus --duration, rounded "
        "down to the minute. Pass it explicitly for byte-identical reruns.",
    )
    parser.add_argument("--duration", type=int, default=300, help="Window length in seconds")
    parser.add_argument("--count", type=int, default=200, help=f"Number of events (1-{MAX_COUNT})")
    parser.add_argument("--batch-size", type=int, default=500, help="Events per request (1-500)")
    parser.add_argument("--dry-run", action="store_true", help="Print JSON lines; send nothing")
    parser.add_argument(
        "--endpoint",
        default=None,
        help=f"API base URL (default ${ENV_ENDPOINT} or http://localhost:8000)",
    )
    return parser


def _default_start(duration: int) -> datetime:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    return now - timedelta(seconds=duration)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    client_factory: ClientFactory | None = None,
) -> int:
    out = stdout or sys.stdout
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 1 <= args.count <= MAX_COUNT:
        parser.error(f"--count must be between 1 and {MAX_COUNT}")
    if not 1 <= args.duration <= 7 * 86_400:
        parser.error("--duration must be between 1 second and 7 days")
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    start = args.start or _default_start(args.duration)
    events = generate_events(
        args.scenario, seed=args.seed, start=start, duration_seconds=args.duration, count=args.count
    )
    if args.dry_run:
        for event in events:
            out.write(json.dumps(event.to_payload(), sort_keys=True) + "\n")
        return 0

    key = os.environ.get(ENV_KEY)
    if not key:
        print(
            f"error: set {ENV_KEY} to a project ingestion key (or use --dry-run)", file=sys.stderr
        )
        return 2
    endpoint = args.endpoint or os.environ.get(ENV_ENDPOINT) or "http://localhost:8000"
    factory = client_factory or (
        lambda ep, k: TracePilotClient(ep, k, max_batch_size=args.batch_size)
    )
    try:
        with factory(endpoint, key) as client:
            result = client.send(events)
    except TracePilotError as exc:  # message never contains the key
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    summary = {
        "scenario": args.scenario,
        "seed": args.seed,
        "start": start.isoformat(),
        "events": args.count,
        "batches": result.batches,
        "accepted": result.accepted,
        "duplicates": result.duplicates,
    }
    out.write(json.dumps(summary) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
