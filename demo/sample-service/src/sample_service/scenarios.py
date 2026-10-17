"""Deterministic scenarios for a fictional shop made of three services.

Same (scenario, seed, start, duration, count) -> byte-identical events. Event ids are
`uuid5(NAMESPACE, f"{scenario}:{seed}:{index}")`, so re-sending a run is idempotent on the
server even if the timestamps differ (e.g. a different --start).

Every event is clearly synthetic: `environment="demo"` and `attributes.synthetic = true`.
No real credentials, hosts or customer data are ever produced.
"""

from __future__ import annotations

import math
import random
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from tracepilot_sdk import LogEvent

NAMESPACE = uuid.UUID("6f1c3c2e-6d1b-5c55-9c3e-7a1f0d1e2b40")  # fixed; never change
ENVIRONMENT = "demo"
BASELINE_MS = 80.0

ROUTES: dict[str, list[tuple[str, str]]] = {
    "web-frontend": [("GET", "/"), ("GET", "/products"), ("GET", "/products/{id}")],
    "checkout-api": [("POST", "/api/cart/items"), ("GET", "/api/cart"), ("POST", "/api/checkout")],
    "payments-worker": [
        ("POST", "/internal/payments/charge"),
        ("POST", "/internal/payments/refund"),
    ],
}
SERVICES = tuple(ROUTES)


@dataclass(frozen=True, slots=True)
class Profile:
    """Traffic shape at a point in the window (progress in [0, 1])."""

    error_rate: float
    median_ms: float
    sigma: float = 0.45
    failure: str = "http_500"  # which failure mode errors represent
    focus_service: str | None = None  # where failures concentrate


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    description: str
    profile: Callable[[float], Profile]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


SCENARIOS: dict[str, Scenario] = {
    s.name: s
    for s in (
        Scenario(
            "normal",
            "Healthy baseline: ~1% errors, ~80 ms median latency",
            lambda t: Profile(error_rate=0.01, median_ms=80),
        ),
        Scenario(
            "latency",
            "Latency regression on checkout-api: ~900 ms median with a long tail",
            lambda t: Profile(
                error_rate=0.02,
                median_ms=900,
                sigma=0.6,
                failure="slow",
                focus_service="checkout-api",
            ),
        ),
        Scenario(
            "http-500",
            "Repeated HTTP 500s from POST /api/checkout (~40%)",
            lambda t: Profile(
                error_rate=0.40, median_ms=120, failure="http_500", focus_service="checkout-api"
            ),
        ),
        Scenario(
            "db-timeout",
            "Database timeouts in payments-worker (~30%, ~5 s each)",
            lambda t: Profile(
                error_rate=0.30,
                median_ms=150,
                failure="db_timeout",
                focus_service="payments-worker",
            ),
        ),
        Scenario(
            "external-api-failure",
            "Upstream payment provider failing with 502/503 (~35%)",
            lambda t: Profile(
                error_rate=0.35, median_ms=200, failure="upstream", focus_service="payments-worker"
            ),
        ),
        Scenario(
            "recovery",
            "Recovery: errors fall 30% -> 1%, latency 600 -> 80 ms",
            lambda t: Profile(
                error_rate=_lerp(0.30, 0.01, t),
                median_ms=_lerp(600, 80, t),
                failure="http_500",
                focus_service="checkout-api",
            ),
        ),
    )
}


def event_id(scenario: str, seed: int, index: int) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{scenario}:{seed}:{index}")


def _hex(rng: random.Random, bits: int) -> str:
    return f"{rng.getrandbits(bits):0{bits // 4}x}"


def _failure(
    kind: str, method: str, path: str, ms: float
) -> tuple[int, str, str, float, dict[str, object]]:
    """(status, level, message, duration_ms, extra attributes) for one failing request."""
    if kind == "db_timeout":
        return (
            504,
            "error",
            f"{method} {path} failed: database query timed out after 5000 ms",
            max(ms, 5000.0),
            {"error.type": "DatabaseTimeout", "db.system": "postgresql"},
        )
    if kind == "upstream":
        return (
            502,
            "error",
            f"{method} {path} failed: payment provider returned 503",
            ms,
            {"error.type": "UpstreamError", "peer.service": "payment-provider (simulated)"},
        )
    if kind == "slow":
        return (
            504,
            "error",
            f"{method} {path} timed out after {ms:.0f} ms",
            ms,
            {"error.type": "GatewayTimeout"},
        )
    return (
        500,
        "error",
        f"{method} {path} -> 500 Internal Server Error",
        ms,
        {"error.type": "UnhandledException", "exception": "ValueError: simulated failure"},
    )


def generate_events(
    scenario: str,
    *,
    seed: int,
    start: datetime,
    duration_seconds: int,
    count: int,
) -> Iterator[LogEvent]:
    """Yield `count` events spread over [start, start + duration) for the scenario."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}")
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    if count < 1 or duration_seconds < 1:
        raise ValueError("count and duration must be positive")
    spec = SCENARIOS[scenario]
    rng = random.Random(f"tracepilot-demo:{scenario}:{seed}")  # str seeds are stable across runs
    step = duration_seconds / count
    for index in range(count):
        progress = index / max(1, count - 1)
        profile = spec.profile(progress)
        offset = index * step + rng.random() * step
        ts = start + timedelta(seconds=offset)
        if profile.focus_service and rng.random() < 0.6:
            service = profile.focus_service
        else:
            service = rng.choice(SERVICES)
        method, path = rng.choice(ROUTES[service])
        affected = profile.focus_service is None or service == profile.focus_service
        median, sigma = (profile.median_ms, profile.sigma) if affected else (BASELINE_MS, 0.45)
        ms = round(rng.lognormvariate(math.log(median), sigma), 1)
        attributes: dict[str, object] = {
            "synthetic": True,
            "scenario": scenario,
            "seed": seed,
            "region": rng.choice(("demo-east", "demo-west")),
        }
        failing = rng.random() < profile.error_rate and affected
        if failing:
            status, level, message, ms, extra = _failure(profile.failure, method, path, ms)
            attributes.update(extra)
        else:
            status = 201 if method == "POST" and rng.random() < 0.3 else 200
            level = "warning" if ms > 2000 else "info"
            message = f"{method} {path} -> {status} in {ms:.0f} ms"
        yield LogEvent(
            event_id=event_id(scenario, seed, index),
            timestamp=ts,
            level=level,  # type: ignore[arg-type]
            service_name=service,
            environment=ENVIRONMENT,
            message=message,
            trace_id=_hex(rng, 128),
            request_id=f"req-{_hex(rng, 64)}",
            duration_ms=ms,
            http_method=method,  # type: ignore[arg-type]
            http_path=path,
            http_status=status,
            attributes=attributes,
        )
